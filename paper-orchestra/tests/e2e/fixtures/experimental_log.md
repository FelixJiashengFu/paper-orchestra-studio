# Experimental log

## Setup
- Benchmark: Kinetics-style human action recognition benchmark (standard train/val split).
- Backbone: frozen video transformer pretrained on images.
- Optimizer: decoupled weight decay (AdamW-style), learning rate 1e-3, 30 epochs, batch size 64.

## Main results (top-1 accuracy, %)
| Method            | Top-1 | Trainable params (M) |
|-------------------|-------|----------------------|
| Full fine-tuning  | 78.1  | 86.0                 |
| Bottleneck adapter| 77.4  | 3.1                  |
| STA (ours)        | 79.3  | 1.2                  |

## Ablation (neighbourhood size)
| Neighbours | Top-1 |
|------------|-------|
| 1          | 78.2  |
| 3          | 79.3  |
| 5          | 79.1  |
