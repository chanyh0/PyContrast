import os
import time
import torch
import dateutil.tz

from tqdm import tqdm
from utils.utils import dice, Logger, Saver, adjust_learning_rate
from config import parse_args
from datetime import datetime
from parallel_functions import train, validate
from datasets.paths import get_paths
from datasets.hdf5 import HDF5Dataset
from datasets.dataset import build_dataset

from torch.utils.data import DataLoader
from models.vnet_parallel import VNet
from torch.nn.parallel import DistributedDataParallel as DDP
from models.mem_moco import RGBMoCo

def main():

    args = parse_args()
    if args.turnon < 0:
        args.pretrain = True
    else:
        args.pretrain = False
    print("Using GPU: {}".format(args.local_rank))
    root_path = 'exps/exp_{}'.format(args.exp)
    if args.local_rank == 0 and not os.path.exists(root_path):
        os.mkdir(root_path)
        os.mkdir(os.path.join(root_path, "log"))
        os.mkdir(os.path.join(root_path, "model"))
    
    base_lr = args.lr  # base learning rate
    batch_size = 1
    max_iterations = 20000

    cell_size = 96  # size of volume we crop patch from
    patch_size = 64
    puzzle_config = 3  # 2 or 3 for 2X2X2 or 3X3X3 puzzle
    puzzle_num = puzzle_config ** 3
    feature_len = 256  #
    iter_num = 0
    sr_feature_size = 32
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
    train_dataset, val_dataset = build_dataset(args)
    args.world_size = len(args.gpu.split(","))
    if args.world_size > 1:
        os.environ['MASTER_PORT'] = args.port
        torch.cuda.set_device(args.local_rank)
        torch.distributed.init_process_group(
            'nccl'
        )
        device = torch.device('cuda:{}'.format(args.local_rank))
        train_sampler = torch.utils.data.distributed.DistributedSampler(train_dataset, num_replicas = len(args.gpu.split(",")), rank = args.local_rank)
    else:
        train_sampler = None
  
    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=args.batch_size, 
        shuffle=(train_sampler is None),
        sampler = train_sampler,
        num_workers=args.num_workers, pin_memory=True)
    
    val_loader = torch.utils.data.DataLoader(
        val_dataset, batch_size=1, 
        num_workers=args.num_workers, pin_memory=True)

    model = VNet(args.n_channels, args.n_classes, input_size = 64, pretrain = True).cuda(args.local_rank)
    model_ema = VNet(args.n_channels, args.n_classes, input_size = 64, pretrain = True).cuda(args.local_rank)
    optimizer = torch.optim.SGD(model.parameters(), lr = args.lr, momentum=0.9, weight_decay=0.0005)
    if args.world_size > 1:
        model = DDP(model, device_ids=[args.local_rank], output_device=args.local_rank, find_unused_parameters=True)

    model.train()
    model_ema = DDP(model_ema, device_ids=[args.local_rank], output_device=args.local_rank, find_unused_parameters=True)
    model_ema.load_state_dict(model.state_dict())
    print("Loaded weights")

    logger = Logger(root_path)
    saver = Saver(root_path, save_freq=args.save_freq)
    contrast = RGBMoCo(128, K = 4096).cuda(args.local_rank)
    criterion = torch.nn.CrossEntropyLoss()

    flag = False
    for epoch in range(args.start_epoch, args.epochs):
        train_sampler.set_epoch(epoch)
        train(model, model_ema, train_loader, optimizer, logger, saver, args, epoch, contrast, criterion)
        validate(model_ema, val_loader, optimizer, logger, saver, args, epoch)
        adjust_learning_rate(args, optimizer, epoch)
        if not flag and args.turnon > 0 and epoch >= args.turnon:
            args.pretrain = True
            train_dataset, val_dataset = build_dataset(args)
            train_sampler = torch.utils.data.distributed.DistributedSampler(train_dataset, num_replicas = len(args.gpu.split(",")), rank = args.local_rank)
            train_loader = torch.utils.data.DataLoader(
                train_dataset, batch_size=args.batch_size, 
                shuffle=(train_sampler is None),
                sampler = train_sampler,
                num_workers=args.num_workers, pin_memory=True)
    
            val_loader = torch.utils.data.DataLoader(
                val_dataset, batch_size=1, 
                num_workers=args.num_workers, pin_memory=True)
            
            flag = True
        

if __name__ == '__main__':
    main()
