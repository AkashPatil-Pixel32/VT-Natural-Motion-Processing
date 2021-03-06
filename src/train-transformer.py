# Copyright (c) 2020-present, Assistive Robotics Lab
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
#

from transformers.training_utils import fit
from transformers.transformers import (
    InferenceTransformerEncoder,
    InferenceTransformer
)
from common.data_utils import load_dataloader
from common.logging import logger
from common.losses import QuatDistance
import torch
from torch import nn, optim
import numpy as np
import argparse

torch.manual_seed(42)
np.random.seed(42)

torch.backends.cudnn.deterministic = False
torch.backends.cudnn.benchmark = False


def parse_args():
    """Parse arguments for module.

    Returns:
        argparse.Namespace: contains accessible arguments passed in to module
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("--task",
                        help=("task for neural network to train on; "
                              "either prediction or conversion"))
    parser.add_argument("--data-path",
                        help=("path to h5 files containing data "
                              "(must contain training.h5 and validation.h5)"))
    parser.add_argument("--representation",
                        help=("will normalize if quaternions, will use expmap "
                              "to quat validation loss if expmap"),
                        default="quaternion")
    parser.add_argument("--full-transformer",
                        help=("will use Transformer with both encoder and "
                              "decoder if true, will only use encoder "
                              "if false"),
                        default=False,
                        action="store_true")
    parser.add_argument("--model-file-path",
                        help="path to model file for saving it after training")
    parser.add_argument("--batch-size",
                        help="batch size for training", default=32)
    parser.add_argument("--learning-rate",
                        help="initial learning rate for training",
                        default=0.001)
    parser.add_argument("--beta-one",
                        help="beta1 for adam optimizer (momentum)",
                        default=0.9)
    parser.add_argument("--beta-two",
                        help="beta2 for adam optimizer", default=0.999)
    parser.add_argument("--seq-length",
                        help=("sequence length for model, will be divided "
                              "by downsample if downsample is provided"),
                        default=20)
    parser.add_argument("--downsample",
                        help=("reduce sampling frequency of recorded data; "
                              "default sampling frequency is 240 Hz"),
                        default=1)
    parser.add_argument("--in-out-ratio",
                        help=("ratio of input/output; "
                              "seq_length / downsample = input length = 10, "
                              "output length = input length / in_out_ratio"),
                        default=1)
    parser.add_argument("--stride",
                        help=("stride used when reading data in "
                              "for running prediction tasks"),
                        default=3)
    parser.add_argument("--num-epochs",
                        help="number of epochs for training", default=1)
    parser.add_argument("--num-heads",
                        help="number of heads in Transformer")
    parser.add_argument("--dim-feedforward",
                        help=("number of dimensions in feedforward layer "
                              "in Transformer"))
    parser.add_argument("--dropout",
                        help="dropout percentage in Transformer")
    parser.add_argument("--num-layers",
                        help="number of layers in Transformer")

    args = parser.parse_args()

    if args.data_path is None:
        parser.print_help()

    return args


if __name__ == "__main__":
    args = parse_args()

    for arg in vars(args):
        logger.info(f"{arg} - {getattr(args, arg)}")

    logger.info("Starting Transformer training...")

    logger.info(f"Device count: {torch.cuda.device_count()}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Training on {device}...")
    seq_length = int(args.seq_length)//int(args.downsample)

    assert seq_length % int(args.in_out_ratio) == 0

    lr = float(args.learning_rate)

    normalize = True
    train_dataloader, norm_data = load_dataloader(args, "training", normalize)
    val_dataloader, _ = load_dataloader(args, "validation", normalize,
                                        norm_data=norm_data)

    encoder_feature_size = train_dataloader.dataset[0][0].shape[1]
    decoder_feature_size = train_dataloader.dataset[0][1].shape[1]

    num_heads = int(args.num_heads)
    dim_feedforward = int(args.dim_feedforward)
    dropout = float(args.dropout)
    num_layers = int(args.num_layers)
    quaternions = (args.representation == "quaternions")

    if args.full_transformer:
        model = InferenceTransformer(decoder_feature_size, num_heads,
                                     dim_feedforward, dropout,
                                     num_layers, quaternions=quaternions)
    else:
        model = InferenceTransformerEncoder(encoder_feature_size, num_heads,
                                            dim_feedforward, dropout,
                                            num_layers, decoder_feature_size,
                                            quaternions=quaternions)

    num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)

    if torch.cuda.device_count() > 1:
        model = nn.DataParallel(model)

    model = model.to(device).double()

    epochs = int(args.num_epochs)
    beta1 = float(args.beta_one)
    beta2 = float(args.beta_two)

    optimizer = optim.AdamW(model.parameters(),
                            lr=lr,
                            betas=(beta1, beta2),
                            weight_decay=0.03)
    scheduler = optim.lr_scheduler.MultiStepLR(optimizer,
                                               milestones=[1, 3],
                                               gamma=0.1)

    dataloaders = (train_dataloader, val_dataloader)
    training_criterion = nn.L1Loss()
    validation_criteria = [nn.L1Loss(), QuatDistance()]

    logger.info(f"Model for training: {model}")
    logger.info(f"Number of parameters: {num_params}")
    logger.info(f"Optimizer for training: {optimizer}")
    logger.info(f"Criterion for training: {training_criterion}")

    fit(model, optimizer, scheduler, epochs, dataloaders, training_criterion,
        validation_criteria, device, args.model_file_path,
        full_transformer=args.full_transformer)

    logger.info("Completed Training...")
    logger.info("\n")
