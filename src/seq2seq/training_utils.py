import matplotlib.ticker as ticker
import torch
import random
import time
import math
import numpy as np
import matplotlib.pyplot as plt
from torch import optim
from common.logging import logger

# importing models
from .seq2seq import *
plt.switch_backend('agg')
torch.manual_seed(0)


class Timer:
    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.interval = self.end - self.start


def get_encoder(num_features, device, hidden_size=64, dropout=0.0, bidirectional=False):
    encoder = EncoderRNN(num_features, hidden_size,
                         dropout=dropout, bidirectional=bidirectional).to(device)
    return encoder


def get_decoder(num_features, device, hidden_size=64, dropout=0.0):
    decoder = DecoderRNN(num_features, hidden_size,
                         num_features, dropout=dropout).to(device)
    return decoder


def get_attn_decoder(num_features, method, device, batch_size=32, hidden_size=64, bidirectional_encoder=False):
    attn = Attention(hidden_size, batch_size, method,
                     bidirectional_encoder=bidirectional_encoder)
    decoder = AttnDecoderRNN(num_features, num_features, hidden_size, hidden_size,
                             attn, bidirectional_encoder=bidirectional_encoder).to(device)
    return decoder


def loss_batch(data, models, opts, criterion, device, scaler=None, teacher_forcing_ratio=0.0, use_attention=False, norm_quaternions=False, average_batch=True):
    encoder, decoder = models
    input_batch, target_batch = data

    input_batch = input_batch.to(device).float()
    target_batch = target_batch.to(device).float()

    if opts is not None:
        encoder.train(), decoder.train()
        encoder_opt, decoder_opt = opts
    else:
        encoder.eval(), decoder.eval()

    loss = 0
    seq_length = input_batch.shape[1]

    input = input_batch.permute(1, 0, 2)
    encoder_outputs, encoder_hidden = encoder(input)

    decoder_hidden = encoder_hidden
    decoder_input = torch.ones_like(target_batch[:, 0, :]).unsqueeze(0)
    EOS = torch.zeros_like(target_batch[:, 0, :]).unsqueeze(0)
    outputs = torch.zeros_like(target_batch)

    use_teacher_forcing = True if random.random() < teacher_forcing_ratio else False
    
    if not average_batch:
        if opts is not None:
            logger.warning("average_batch must be true when training")
            sys.exit()
        losses = [0 for i in range(target_batch.shape[0])]

    for t in range(seq_length):

        if use_attention:
            decoder_output, decoder_hidden = decoder(
                decoder_input, decoder_hidden, encoder_outputs)
        else:
            decoder_output, decoder_hidden = decoder(
                decoder_input, decoder_hidden)

        target = target_batch[:, t, :].unsqueeze(0)

        output = decoder_output

        if norm_quaternions:
            original_shape = output.shape

            output = output.contiguous().view(-1,4)
            output = F.normalize(output, p=2, dim=1).view(original_shape)

        outputs[:, t, :] = output

        if use_teacher_forcing:
            decoder_input = target
        else:
            if torch.all(torch.eq(decoder_output, EOS)):
                break
            decoder_input = output.detach()

    loss = criterion(outputs, target_batch)

    if opts is not None:
        loss.backward()

        encoder_opt.step()
        encoder_opt.zero_grad()

        decoder_opt.step()
        decoder_opt.zero_grad()

    if average_batch:
        return loss.item()
    else:
        losses = []
        for b in range(outputs.shape[0]):
            sample_loss = criterion(outputs[b,:], target_batch[b,:])
            losses.append(sample_loss.item())

        return losses


def fit(models, optims, epochs, dataloaders, training_criterion, validation_criteria, schedulers, device, model_file_path,
        teacher_forcing_ratio=0.0,
        use_attention=False,
        norm_quaternions=False,
        schedule_rate=1.0):

    train_dataloader, val_dataloader = dataloaders

    num_batches = len(train_dataloader)
    batch_size = len(train_dataloader.dataset[0])

    plot_losses = []
    min_val_loss = math.inf
    for epoch in range(epochs):
        losses = []
        total_time = 0

        logger.info("Epoch {} / {}".format(str(epoch+1), str(epochs)))

        for index, data in enumerate(train_dataloader, 0):
            with Timer() as timer:
                loss = loss_batch(data, models,
                                  optims, training_criterion, device,
                                  use_attention=use_attention,
                                  norm_quaternions=norm_quaternions)

                losses.append(loss)
            total_time += timer.interval
            if index % (len(train_dataloader) // 10) == 0:
                logger.info("Total time elapsed: {} - Batch Number: {} / {} - Training loss: {}".format(str(total_time), 
                                                                                                       str(index), 
                                                                                                       str(num_batches), 
                                                                                                       str(loss)))
        val_loss = []
        for validation_criterion in validation_criteria:
            with torch.no_grad():
                val_losses = [loss_batch(data, models, 
                                         None, validation_criterion, device, 
                                         use_attention=use_attention, 
                                         norm_quaternions=norm_quaternions)
                                         for _, data in enumerate(val_dataloader, 0)]

            val_loss.append(np.sum(val_losses) / len(val_losses))


        loss = np.sum(losses) / len(losses)

        for scheduler in schedulers:
            scheduler.step()

        logger.info("Training Loss: {} - Val Loss: {}".format(str(loss), ", ".join(map(str, val_loss))))

        teacher_forcing_ratio *= schedule_rate
        if val_loss[0] < min_val_loss:
            min_val_loss = val_loss[0]
            logger.info("Saving model to {}".format(model_file_path))
            torch.save({
                'encoder_state_dict': models[0].state_dict(),
                'decoder_state_dict': models[1].state_dict(),
                'optimizerA_state_dict': optims[0].state_dict(),
                'optimizerB_state_dict': optims[1].state_dict(),
            }, model_file_path)
