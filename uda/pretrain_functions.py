import torch

from tqdm import tqdm
from utils import cross_entropy_3d, dice, AverageMeter, compute_loss_accuracy
from models import mem_moco

try:
    from apex import amp, optimizers
except ImportError:
    pass


def validate(model, loader, optimizer, logger, saver, args, epoch):
	model.eval()
	
	batch_time = AverageMeter()
	data_time = AverageMeter()
	loss_meter = AverageMeter()
	acc_meter = AverageMeter()
	loss_jig_meter = AverageMeter()
	acc_jig_meter = AverageMeter()

	for i, batch in enumerate(loader):
		index = batch['index']
		volume = batch['image'].cuda()
		volume = volume.view((-1,) + volume.shape[2:])

		volume2 = batch['image_2'].cuda()
		volume2 = volume2.view((-1,) + volume2.shape[2:])

		q = model.pretrain_forward(volume)
		k = model_ema.pretrain_forward(volume)

		output = contrast(q, k, all_k=None)

		loss = losses[0]
		update_loss = losses[0]
		update_acc = accuracies[0]
		update_loss_jig = torch.tensor([0.0])
		update_acc_jig = torch.tensor([0.0])

		loss_meter.update(update_loss.item(), args.batch_size)
		loss_jig_meter.update(update_loss_jig.item(), args.batch_size)
		acc_meter.update(update_acc[0], args.batch_size)
		acc_jig_meter.update(update_acc_jig[0], args.batch_size)

	saver.save(epoch, {
		'state_dict': model.state_dict(),
		'acc': acc_meter.avg,
		'optimizer_state_dict': optimizer.state_dict(),
		'amp': amp.state_dict() if args.amp else None
	}, acc_meter.avg)

	tqdm.write("[Epoch {}] test acc: {}, test jig acc".format(epoch, acc_meter.avg, acc_jig_meter.avg))



def pretrain(model, model_ema, loader, optimizer, logger, args, epoch, contrast, criterion):
	model.train()
	model_ema.eval()

	batch_time = AverageMeter()
	data_time = AverageMeter()
	loss_meter = AverageMeter()
	acc_meter = AverageMeter()
	loss_jig_meter = AverageMeter()
	acc_jig_meter = AverageMeter()

	def set_bn_train(m):
		classname = m.__class__.__name__
		if classname.find('BatchNorm') != -1:
			m.train()
	model_ema.apply(set_bn_train)

	for i, batch in enumerate(tqdm(loader)):
		index = batch['index']
		volume = batch['image'].cuda()
		volume = volume.view((-1,) + volume.shape[2:])

		volume2 = batch['image_2'].cuda()
		volume2 = volume2.view((-1,) + volume2.shape[2:])

		q = model.pretrain_forward(volume)
		k = model_ema.pretrain_forward(volume)

		output = contrast(q, k, all_k=None)
		losses, accuracies = compute_loss_accuracy(
						logits=output[:-1], target=output[-1],
						criterion=criterion)
		loss = losses[0]
		update_loss = losses[0]
		update_acc = accuracies[0]
		update_loss_jig = torch.tensor([0.0])
		update_acc_jig = torch.tensor([0.0])

		#loss = cross_entropy_3d(output, label)

		optimizer.zero_grad()
		if args.amp:
			with amp.scale_loss(loss, optimizer) as scaled_loss:
					scaled_loss.backward()
		else:
			loss.backward()
		optimizer.step()

		loss_meter.update(update_loss.item(), args.batch_size)
		loss_jig_meter.update(update_loss_jig.item(), args.batch_size)
		acc_meter.update(update_acc[0], args.batch_size)
		acc_jig_meter.update(update_acc_jig[0], args.batch_size)
		momentum_update(model.module, model_ema)
		if i % args.print_freq == 0:
			tqdm.write('Train: [{0}][{1}/{2}]\t'
						  'l_I {loss.val:.3f} ({loss.avg:.3f})\t'
						  'a_I {acc.val:.3f} ({acc.avg:.3f})\t'
						  'l_J {loss_jig.val:.3f} ({loss_jig.avg:.3f})\t'
						  'a_J {acc_jig.val:.3f} ({acc_jig.avg:.3f})'.format(
						   epoch, idx + 1, len(train_loader), loss=loss_meter, acc=acc_meter,
						   loss_jig=loss_jig_meter, acc_jig=acc_jig_meter))

		logger.log("pretrain/loss", update_loss.item())
		logger.log("pretrain/acc", update_acc[0])
		logger.step()

	tqdm.write("Train: [{}] avg loss: {}, avg acc: {}, avg jig acc:".format(epoch, acc_meter.avg, acc_jig_meter.avg))

def momentum_update(model, model_ema, m = 0.999):
        """ model_ema = m * model_ema + (1 - m) model """
        for p1, p2 in zip(model.parameters(), model_ema.parameters()):
            p2.data.mul_(m).add_(1 - m, p1.detach().data)
