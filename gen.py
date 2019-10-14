import argparse
import time
import math
import torch
from torch import nn
import torch.optim as optim
from torch.optim.lr_scheduler import ReduceLROnPlateau
import torch.nn.functional as F
from torch.utils.data import DataLoader
from model_gen import *
from rdkit import Chem


def train():
    global train_iter
    model.train()
    total_loss = 0
    start_time = time.time()
    for data, label in train_iter:
        targets = data[:, 1:].cuda()
        inputs = data[:, :-1].cuda()
        optimizer.zero_grad()
        output = model(inputs)

        final_output = output.contiguous().view(-1, n_words)
        final_target = targets.contiguous().view(-1)

        loss = criterion(final_output, final_target)
        loss.backward()
        optimizer.step()

        total_loss += loss.item()

    cur_loss = total_loss / len(train_iter)
    elapsed = time.time() - start_time
    print('| epoch {:3d} | ms/batch {:5.4f} | train loss {:5.6f} |'.format
          (epoch, elapsed * 1000 / len(train_iter), cur_loss))


def evaluate(data_iter):
    model.eval()
    total_loss = 0

    for data, label in data_iter:
        targets = data[:, 1:].cuda()
        inputs = data[:, :-1].cuda()
        output = model(inputs)

        final_output = output.contiguous().view(-1, n_words)
        final_target = targets.contiguous().view(-1)

        loss = criterion(final_output, final_target)
        total_loss += loss.item()

    return total_loss / len(data_iter)


def sample(idx2word, smi, num_samples):
    model.eval()
    n_words = len(idx2word)

    n = 0
    ss = []
    lss = 0
    for i in range(num_samples):
        input = torch.ones(1, 1, dtype=torch.long).cuda()
        word = '&'
        while word[-1] != '\n':
            output = model(input)
            final_output = output.contiguous().view(-1, n_words)
            word_id = torch.multinomial(F.softmax(final_output[-1, :], dim=-1), num_samples=1).unsqueeze(0)
            input = torch.cat((input, word_id), dim=1)
            word += idx2word[word_id.item()]

        if bool(Chem.MolFromSmiles(word[1:])):
            n += 1
            if word[1:] not in smi and word[1:] not in ss:
                ss += [word[1:]]
        if i != 0 and i % 10000 == 0:
            print(len(ss) - lss)
            lss = len(ss)
    print(n / num_samples)
    return ss


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Generative Modeling')
    parser.add_argument('--batch_size', type=int, default=32,
                        metavar='N', help='batch size (default: 32)')
    parser.add_argument('--cuda', action='store_false',
                        help='use CUDA (default: True)')
    parser.add_argument('--dropout', type=float, default=0.2,
                        help='dropout applied to layers (default: 0.2)')
    parser.add_argument('--emb_dropout', type=float, default=0.1,
                        help='dropout applied to the embedded layer (default: 0.1)')
    parser.add_argument('--epochs', type=int, default=200,
                        help='upper epoch limit (default: 200)')
    parser.add_argument('--ksize', type=int, default=3,
                        help='kernel size (default: 3)')
    parser.add_argument('--emsize', type=int, default=32,
                        help='size of word embeddings (default: 32)')
    parser.add_argument('--levels', type=int, default=5,
                        help='# of levels (default: 4)')
    parser.add_argument('--lr', type=float, default=0.001,
                        help='initial learning rate (default: 0.001)')
    parser.add_argument('--nhid', type=int, default=256,
                        help='number of hidden units per layer (default: 256)')
    parser.add_argument('--optim', type=str, default='Adam',
                        help='optimizer type (default: Adam)')
    parser.add_argument('--save_name', type=str, default='gen.pt',
                        help='the name of save model')
    args = parser.parse_args()

    print(args)

    if torch.cuda.is_available():
        if not args.cuda:
            print("WARNING: You have a CUDA device, so you should probably run with --cuda")

    torch.manual_seed(1024)
    word2idx, idx2word = torch.load("data/opv_dic.pt")
    train_data, val_data, test_data = torch.load("data/opv_data.pt")
    train_iter = DataLoader(train_data, args.batch_size, shuffle=True)
    val_iter = DataLoader(val_data, args.batch_size, shuffle=False)
    test_iter = DataLoader(test_data, args.batch_size, shuffle=False)
    n_words = len(word2idx)

    model = GEN(args.emsize, n_words, n_words, hid_size=args.nhid, n_levels=args.lvels,
                kernel_size=args.ksize, emb_dropout=args.emb_dropout, dropout=args.dropout )

    if args.cuda:
        model.cuda()

    criterion = nn.CrossEntropyLoss()
    optimizer = getattr(optim, args.optim)(model.parameters(), lr=args.lr)
    scheduler = ReduceLROnPlateau(optimizer, 'min')

    best_vloss = 100
    try:
        for epoch in range(1, args.epochs + 1):
            epoch_start_time = time.time()
            train()
            val_loss = evaluate(val_iter)
            scheduler.step(val_loss)

            print('-' * 89)
            print('| end of epoch {:3d} | time: {:5.4f}s | valid loss {:5.6f} | valid ppl {:8.4f}'.format
                  (epoch, (time.time() - epoch_start_time), val_loss, math.exp(val_loss)))
            print('-' * 89)

            if val_loss < best_vloss:
                print('Save model!\n')
                torch.save( model.state_dict(), "results/saved_models/" + str(args.levels) + args.save_name)
                best_vloss = val_loss

    except KeyboardInterrupt:
        print('-' * 89)
        print('Exiting from training early')

    model.load_state_dict(torch.load("results/saved_models/" + str(args.levels) + args.save_name), strict=True)
    test_loss = evaluate(test_iter)
    print('=' * 89)
    print('| End of training | test loss {:5.4f} | test ppl {:8.4f}'.format(test_loss, math.exp(test_loss)))
    print('=' * 89)

    with open('/data/home/psp/TCN/non-fullerene/data/smi_c.txt', 'r') as smi:
        smiles = smi.readlines()
    ss = sample(idx2word, smiles, num_samples=100000)
    with open("results/" + str(args.levels) + 'sample.txt', 'w') as f:
        f.writelines(ss)