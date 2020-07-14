import os
import torch
import shutil
import tensorboardX

import numpy as np
import torch.nn.functional as F

def cross_entropy_3d(x, y, w=None):
	assert len(x.shape) == len(y.shape)
	n, c, _, _, _ = x.size()
	x_t = torch.transpose(torch.transpose(torch.transpose(x, 1, 2), 2, 3), 3, 4).contiguous().view(-1, c)
	y_t = torch.transpose(torch.transpose(torch.transpose(y, 1, 2), 2, 3), 3, 4).contiguous().view(-1).long()
	loss = F.cross_entropy(x_t, y_t, weight=w)
	return loss
def dice(x, y, eps=1e-7):
  intersect = np.sum(np.sum(np.sum(x * y)))
  y_sum = np.sum(np.sum(np.sum(y)))
  x_sum = np.sum(np.sum(np.sum(x)))
  return 2 * intersect / (x_sum + y_sum + eps)

class Logger(object):

	def __init__(self, path):
		self.global_step = 0
		self.logger = tensorboardX.SummaryWriter(os.path.join(path, "log"))

	def log(self, name, value):
		self.logger.add_scalar(name, value, self.global_step)

	def step(self, name):
		self.global_step += 1

	def close(self):
		self.logger.close()

class Saver(object):

	def __init__(self, path):
		self.path = path
		self.best_dice = 0
	def save(self, epoch, states, dice):
		torch.save(states, os.path.join(self.path, 'model', 'checkpoint_{}.pth.tar'.format(epoch)))
		if dice > self.best_dice:
			torch.save(states, os.path.join(self.path, 'model', 'checkpoint_best.pth.tar'))
			self.best_dice = dice