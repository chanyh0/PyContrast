import os
import torch
import math
import sys
import cv2
import argparse
import numpy as np
import pandas as pd
import torch.nn.functional as F
from torch.autograd import Variable
from utils import load_data_test, dice, visualize, construct_PE
from datasets.paths import get_test_paths
from models.vnet import VNet
from collections import OrderedDict

parser = argparse.ArgumentParser(description='Testing VNet.')
parser.add_argument('--dataset', type=str, default='msd_pancreas', help='The dataset to be trained')
parser.add_argument('--iter', type=int, default=80000, help='The iteration of model to be tested')
parser.add_argument('--exp', type=int, default=1, help='index of experiment')
parser.add_argument('--gpu', type=str, default='0', help='GPU to be used')
parser.add_argument('--method', type=str, default='vote', help='testing method to be used, max or vote')
parser.add_argument('--lambda_pe', type=float, default=0.0, help='position encoding weight')
parser.add_argument('--load-path', type=str)
args = parser.parse_args()

classes = ['Pancreas']
os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
print('processing dataset {}'.format(args.dataset))

root_dir, list_path = get_test_paths(args.dataset)


stride = 20
n_class = 2

votesave_path = os.path.join(snapshot_root, 'votemap', os.path.basename(snapshot_path))
os.makedirs(votesave_path, exist_ok=True)

patch_size = 64

if __name__ == "__main__":
    net = VNet(args.n_channels, args.n_classes).cuda()
    #net = torch.load(snapshot_path)
    
    dices = []
    dice_for_cases = []
    case_list = []
    sys.stdout.flush()

    # read the list path from the cross validation
    image_list = open(list_path).readlines()
    assert os.path.exists(args.load_path)
    state_dict = torch.load(args.load_path, map_location="cpu")
    new_state_dict = OrderedDict()
    for key in state_dict.keys():
        new_state_dict[key[7:]] = state_dict[key]
    
    net.load_state_dict(new_state_dict)
    net.cuda()
    net.eval()
    
    # test passed for the first case
    for i in range(0, len(image_list)):
        file_name = image_list[i].strip('\n')
        if '/' in file_name:
            file_name = os.path.basename(file_name)
        case_list.append(file_name)
        imglabelpath = os.path.join(root_dir, file_name)
        image, label = load_data_test(imglabelpath, dataset=args.dataset, convert_msd=False)
        print(label.shape)
        map_name = os.path.join(votesave_path, file_name + '.npz')
        w, h, d = image.shape
        if w < patch_size:
            origin_size = image.shape
            image = np.pad(image, ((0, patch_size - w), (0, 0), (0, 0)), 'minimum')
            padded = True
        else:
            padded = False
        w, h, d = image.shape
        sx = math.floor((w - patch_size) / stride) + 1
        sy = math.floor((h - patch_size) / stride) + 1
        sz = math.floor((d - patch_size) / stride) + 1
        vote_map = torch.zeros((n_class, w, h, d), dtype=torch.float).cuda()
        vote_map[0] += 0.1
        image = Variable(torch.from_numpy(image.astype(np.float32)).cuda())
        # time_map = np.zeros(image.shape).astype(np.float32)
        for x in range(0, sx):
            xs = stride * x
            for y in range(0, sy):
                ys = stride * y
                for z in range(0, sz):
                    zs = stride * z
                    test_patch = image[xs:xs + patch_size, ys:ys + patch_size, zs:zs + patch_size]
                    test_patch = construct_PE(test_patch, args.lambda_pe)
                    output = net(test_patch)
                    if type(output) == tuple:
                        output = output[0]
                    output = output.squeeze(0)

                    # vote strategy
                    if args.method == 'vote':
                        output = output.argmax(dim=0).float()
                        # voting strategy for the over-lapped region
                        for i in range(n_class):
                            vote_map[i, xs:xs + patch_size, ys:ys + patch_size, zs:zs + patch_size] = \
                                vote_map[i, xs:xs + patch_size, ys:ys + patch_size, zs:zs + patch_size] + (
                                            output == i).float()
                    # max strategy for the over-lapped region
                    else:
                        vote_map[:, xs:xs + patch_size, ys:ys + patch_size, zs:zs + patch_size] = torch.max(
                            vote_map[:, xs:xs + patch_size, ys:ys + patch_size, zs:zs + patch_size], output.data)
        # save the output
        vote_map = vote_map.argmax(dim=0)
        if padded:
            vote_map = vote_map[:origin_size[0], :origin_size[1], :origin_size[2]]

        if vote_map.shape != label.shape:
            expected_size = label.shape
            vote_map = vote_map.reshape((1, 1,) + vote_map.shape)
            vote_map = F.interpolate(vote_map.float(), size=expected_size, mode='nearest')[0, 0, :, :, :].to(
                torch.uint8)

        vote_map = vote_map.cpu().data.numpy()
        np.savez(map_name, vote_map=vote_map, label=label)

        # visualize
        image = image.cpu().data
        if image.shape != label.shape:
            expected_size = label.shape
            image = image.reshape((1, 1,) + image.shape)
            image = F.interpolate(image.float(), size=expected_size, mode='trilinear', align_corners=False)[0, 0, :, :, :]
        image = image.numpy()
        vis = visualize(image, vote_map, label, n_class=2, ratio=1.)
        vis_path = os.path.join(votesave_path, file_name)
        os.makedirs(vis_path, exist_ok=True)
        for z_i, im in enumerate(vis):
            cv2.imwrite(os.path.join(vis_path, '{}.jpg'.format(z_i)), im)

        result_print = ''
        for c in range(1, n_class):
            d = dice(vote_map == c, label == c)
            result_print += ' \tdsc {}: {:.4f};'.format(c, d)
            dices.append(d)

        print(file_name + result_print)

    case_list += ['average']
    mean_dice = np.array(dices).mean()
    dices.append(mean_dice)

    print('saving volumes to {}'.format(
        os.path.join(snapshot_root, '{}_{}.csv'.format(args.dataset, os.path.basename(snapshot_path)))))
    pd.DataFrame(data={'name': case_list, 'dsc': dices}).to_csv(
        os.path.join(snapshot_root, '{}_{}.csv'.format(args.dataset, os.path.basename(snapshot_path))), index=False)

    print('Average DSC: {:.5f}'.format(mean_dice))
