import torch
import torch.nn as nn
import torch.nn.functional as F

from .utils import Normalize, JigsawHead

def passthrough(x, **kwargs):
    return x

def ELUCons(elu, nchan):
    if elu:
        return nn.ELU(inplace=True)
    else:
        return nn.PReLU(nchan)

# normalization between sub-volumes is necessary
# for good performance
class ContBatchNorm3d(nn.modules.batchnorm._BatchNorm):
    def _check_input_dim(self, input):
        if input.dim() != 5:
            raise ValueError('expected 5D input (got {}D input)'
                             .format(input.dim()))
        # super(ContBatchNorm3d, self)._check_input_dim(input)

    def forward(self, input):
        self._check_input_dim(input)
        return F.batch_norm(
            input, self.running_mean, self.running_var, self.weight, self.bias,
            True, self.momentum, self.eps)


class LUConv(nn.Module):
    def __init__(self, nchan, elu):
        super(LUConv, self).__init__()
        self.relu1 = ELUCons(elu, nchan)
        self.conv1 = nn.Conv3d(nchan, nchan, kernel_size=5, padding=2)
        self.bn1 = ContBatchNorm3d(nchan)

    def forward(self, x):
        out = self.relu1(self.bn1(self.conv1(x)))
        return out


def _make_nConv(nchan, depth, elu):
    layers = []
    for _ in range(depth):
        layers.append(LUConv(nchan, elu))
    return nn.Sequential(*layers)


class InputTransition(nn.Module):
    def __init__(self, inChans, elu, outChans=16):
        super().__init__()
        self.conv = torch.nn.Sequential(
            torch.nn.Conv3d(inChans, outChans, 3, padding=1),
            ContBatchNorm3d(outChans),
            ELUCons(elu, outChans),

            torch.nn.Conv3d(outChans, outChans, 3, padding=1),
            ContBatchNorm3d(outChans),
            ELUCons(elu, outChans),
        )

    def forward(self, x):
        x = self.conv(x)

        return x


class DownTransition(nn.Module):
    def __init__(self, inChans, nConvs, elu, dropout=False):
        super(DownTransition, self).__init__()
        outChans = 2 * inChans
        self.down_conv = nn.Conv3d(inChans, outChans, kernel_size=2, stride=2)
        self.bn1 = ContBatchNorm3d(outChans)
        self.do1 = passthrough
        self.relu1 = ELUCons(elu, outChans)
        self.relu2 = ELUCons(elu, outChans)
        if dropout:
            self.do1 = nn.Dropout3d()
        self.ops = _make_nConv(outChans, nConvs, elu)

    def forward(self, x):
        down = self.relu1(self.bn1(self.down_conv(x)))
        out = self.do1(down)
        out = self.ops(out)
        out = self.relu2(torch.add(out, down))
        return out


class UpTransition(nn.Module):
    def __init__(self, inChans, outChans, nConvs, elu, dropout=False):
        super(UpTransition, self).__init__()
        self.up_conv = nn.ConvTranspose3d(inChans, outChans // 2, kernel_size=2, stride=2)
        self.bn1 = ContBatchNorm3d(outChans // 2)
        self.do1 = passthrough
        # self.do2 = nn.Dropout3d()
        self.do2 = passthrough
        self.relu1 = ELUCons(elu, outChans // 2)
        self.relu2 = ELUCons(elu, outChans)
        if dropout:
            self.do1 = nn.Dropout3d()
        self.ops = _make_nConv(outChans, nConvs, elu)

    def forward(self, x, skipx):
        out = self.do1(x)
        skipxdo = self.do2(skipx)
        out = self.relu1(self.bn1(self.up_conv(out)))
        xcat = torch.cat((out, skipxdo), 1)
        out = self.ops(xcat)
        out = self.relu2(torch.add(out, xcat))
        return out


class OutputTransition(nn.Module):
    def __init__(self, inChans, elu, n_classes):
        super(OutputTransition, self).__init__()
        self.conv1 = nn.Conv3d(inChans, inChans // 2, kernel_size=3, padding=1)
        self.bn1 = ContBatchNorm3d(inChans // 2)
        self.conv2 = nn.Conv3d(inChans // 2, n_classes, kernel_size=1)
        self.relu1 = ELUCons(elu, inChans // 2)
        print(n_classes)

    def forward(self, x):
        # convolve 32 down to n_classes channels
        out = self.relu1(self.bn1(self.conv1(x)))
        out = self.conv2(out)
        return out



class VNet(nn.Module):
    # the number of convolutions in each layer corresponds
    # to what is in the actual prototxt, not the intent
    def __init__(self, n_channels, n_classes, input_size = 128, elu=False, pretrain = False, feat_dim=128, jigsaw = False):
        super(VNet, self).__init__()
        self.in_tr = InputTransition(n_channels, elu)
        self.down_tr32 = DownTransition(16, 1, elu)
        self.down_tr64 = DownTransition(32, 2, elu)
        self.down_tr128 = DownTransition(64, 3, elu)
        self.down_tr256 = DownTransition(128, 2, elu)
        self.up_tr256 = UpTransition(256, 256, 2, elu)
        self.up_tr128 = UpTransition(256, 128, 2, elu)
        self.up_tr64 = UpTransition(128, 64, 1, elu)
        self.up_tr32 = UpTransition(64, 32, 1, elu)
        self.out_tr = OutputTransition(32, elu, n_classes)
        self.pretrain = pretrain
        self.jigsaw = jigsaw
        if self.pretrain:
            self.head = nn.Sequential(
                nn.Linear(input_size * input_size // 2, input_size * input_size // 2),
                nn.ReLU(inplace=True),
                nn.Linear(input_size * input_size // 2, feat_dim),
                Normalize(2)
            )

            if self.jigsaw:
                self.head_jig = JigsawHead(dim_in=int(16384),
                                   dim_out=feat_dim,
                                   head="mlp")
    def normal_forward(self, x):
        out16 = self.in_tr(x)
        out32 = self.down_tr32(out16)
        out64 = self.down_tr64(out32)
        out128 = self.down_tr128(out64)
        out256 = self.down_tr256(out128)
        out = self.up_tr256(out256, out128)
        out = self.up_tr128(out, out64)
        out = self.up_tr64(out, out32)
        out = self.up_tr32(out, out16)
        out = self.out_tr(out)
        return out, out256

    def forward(self, x, encode=False):
        if encode:
            return self.encode(x)
        else:
            return self.normal_forward(x)

    def encode(self, x):
        out16 = self.in_tr(x)
        out32 = self.down_tr32(out16)
        out64 = self.down_tr64(out32)
        out128 = self.down_tr128(out64)
        out256 = self.down_tr256(out128)
        # out256: (8, 256, 4, 4, 4)
        # TODO: if we need a pool here?
        out256 = F.max_pool3d(out256, 2, stride = 2)
        out256 = out256.view(out256.shape[0], -1)
        return self.head(out256)

    def pretrain_forward(self, x, x_jig=None):
        #print(x.shape)
        x = self.encode(x)
        #print(x.shape)
        feat = self.head(x)
        if self.jigsaw:
            feat_jig = self.head_jig(self.encode(x_jig))
            return feat, feat_jig
        else:
            return feat
        
