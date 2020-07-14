import os
import h5py
import numpy as np
from tqdm import tqdm
from torch.utils.data import Dataset

from paths import get_paths
from transforms import get_transform

class DatasetInstance(Dataset):

	def __init__(self, list_file, root_dir, transform=None, 
		need_non_zero_label = True, is_binary = False, jigsaw_transform = None):
		self.image_list = open(list_file).readlines()
		self.image_list = [os.path.basename(line.strip()) for line in self.image_list]
		self.image_list = [line for line in self.image_list if line.endswith('.h5')]

		self.root_dir = root_dir
		self.read_label = read_label
		self.transform = transform

		self.two_crop = True
		self.use_jigsaw = False #TODO

		print('read {} images'.format(len(self.image_list)))

	def __len__(self):
		return len(self.image_list)

	def __getitem__(self, index):
		img_name = os.path.join(self.root_dir, self.image_list[idx])
		data = h5py.File(img_name, 'r')
		image = np.array(data['image'], dtype=np.float32) 
		label = np.array(data['label'])
		
		data.close()
		sample_pre_transform = {'image': image, 'label': label}

		if self.transform is not None:
			sample = self.transform(sample_pre_transform)
			if self.two_crop:
				sample2 = self.transform(sample_pre_transform)
				sample['image_2'] = sample2['image_2']
				sample['label_2'] = sample2['label_2']
		else:
			img = image

		if self.use_jigsaw:
			 jigsaw_img = self.jigsaw_transform(sample_pre_transform)


		sample['index'] = index
		return sample

def build_dataloader(args):
	train_root, train_list, test_root, test_list = get_paths(args.dataset)
	train_transform, test_transform = get_transform(args)

	train_dataset = DatasetInstance(train_list, train_root, transform=train_transform)

    test_dataset = DatasetInstance(test_list, test_root, transform=test_transform)

    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=args.batch_size, 
        shuffle=True,
        num_workers=args.num_workers, pin_memory=True)
    test_loader = torch.utils.data.DataLoader(
        test_dataset, batch_size=1, 
        num_workers=args.num_workers, pin_memory=True)
    return train_loader, test_loader