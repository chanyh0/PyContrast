import os
import time
import torch
import dateutil.tz

from tqdm import tqdm
from utils import dice, Logger, Saver, adjust_learning_rate
from config import parse_args
from datetime import datetime
from functions import train, validate
from datasets.paths import get_paths
from datasets.hdf5 import HDF5Dataset
from datasets.dataset import build_dataset

from torch.utils.data import DataLoader
from models.vnet import VNet


try:
	from apex import amp, optimizers
	from apex.parallel import DistributedDataParallel as DDP
	apex = True
except ImportError:
    from torch.nn.parallel import DistributedDataParallel as DDP
    apex = False
def main():

	args = parse_args()
	args.pretrain = False
	print("Using GPU: {}".format(args.local_rank))
	os.environ['MASTER_PORT'] = args.port
	torch.cuda.set_device(args.local_rank)
	torch.distributed.init_process_group(
		'nccl'
	)
	device = torch.device('cuda:{}'.format(args.local_rank))
	now = datetime.now(dateutil.tz.tzlocal())
	timestamp = now.strftime('%Y_%m_%d_%H_%M_%S')
	root_path = 'exps/exp{}_{}'.format(args.exp, timestamp)
	if not os.path.exists(root_path):
		os.mkdir(root_path)
		os.mkdir(os.path.join(root_path, "log"))
		os.mkdir(os.path.join(root_path, "model"))
	
	base_lr = args.lr  # base learning rate
	batch_size = 1
	max_iterations = 40000

	cell_size = 96  # size of volume we crop patch from
	patch_size = 64
	puzzle_config = 3  # 2 or 3 for 2X2X2 or 3X3X3 puzzle
	puzzle_num = puzzle_config ** 3
	feature_len = 256  #
	iter_num = 0
	sr_feature_size = 32
	os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
	train_dataset, val_dataset = build_dataset(args)
	train_sampler = torch.utils.data.distributed.DistributedSampler(train_dataset, num_replicas = len(args.gpu.split(",")), rank = args.local_rank)
	train_loader = torch.utils.data.DataLoader(
		train_dataset, batch_size=args.batch_size, 
		shuffle=False,
		sampler = train_sampler,
		num_workers=args.num_workers, pin_memory=True)
    
	val_loader = torch.utils.data.DataLoader(
		val_dataset, batch_size=1, 
		num_workers=args.num_workers, pin_memory=True)
	model = VNet(args.n_channels, args.n_classes).cuda(args.local_rank)
	
	optimizer = torch.optim.SGD(model.parameters(), lr = args.lr, momentum=0.9, weight_decay=0.0005)
	#scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, 0.7)
 
	model = DDP(model)
 
	model.train()
	print("Loaded weights")

	logger = Logger(root_path)
	saver = Saver(root_path)

	for epoch in range(args.start_epoch, args.epochs):
		train(model, train_loader, optimizer, logger, args, epoch)
		validate(model, val_loader, optimizer, logger, saver, args, epoch)
		adjust_learning_rate(args, optimizer, epoch)


if __name__ == '__main__':
	main()
