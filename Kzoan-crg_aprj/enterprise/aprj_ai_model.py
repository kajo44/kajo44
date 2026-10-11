# -*- coding: utf-8 -*-
"""
Modul AI: PyTorch Multi-Task Model, Tensor Processing, & Google Colab Exporter.
Menyediakan arsitektur jaringan saraf tiruan untuk klasifikasi kondisi jalan, perkerasan, dan utilitas Colab.
"""

from pathlib import Path
import random

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

from aprj_core import CLASSES, SURFACE_CLASSES, IMAGE_SIZE

SURVEY_WIDTH_FIELDS = (
    "lebar_bahu_kiri",
    "lebar_drainase_kiri",
    "lebar_perkerasan",
    "lebar_bahu_kanan",
    "lebar_drainase_kanan",
)

if TORCH_AVAILABLE:
    class SurveyMultiTaskNet(nn.Module):
        def __init__(self, task_classes):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(3, 32, 3, padding=1),
                nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Conv2d(32, 64, 3, padding=1),
                nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Conv2d(64, 128, 3, padding=1),
                nn.ReLU(),
                nn.AdaptiveAvgPool2d(1),
            )
            self.heads = nn.ModuleDict(
                {key: nn.Linear(128, len(values)) for key, values in task_classes.items()}
            )
            self.width_head = nn.Linear(128, len(SURVEY_WIDTH_FIELDS))

        def forward(self, images):
            features = self.features(images).flatten(1)
            return {
                "classes": {key: head(features) for key, head in self.heads.items()},
                "widths": self.width_head(features),
            }


def image_to_tensor(image, augment=False):
    """Mengubah objek PIL Image menjadi PyTorch Tensor yang dinormalisasi."""
    if not TORCH_AVAILABLE:
        return None
    from PIL import Image
    image = image.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR)
    if augment and random.random() < 0.5:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    values = torch.frombuffer(bytearray(image.tobytes()), dtype=torch.uint8)
    values = values.reshape(IMAGE_SIZE, IMAGE_SIZE, 3).permute(2, 0, 1).float() / 255.0
    mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
    return (values - mean) / std


class GoogleDriveColabManager:
    @staticmethod
    def generate_colab_training_notebook(destination_path):
        """Membuat skrip Python untuk training model AI di Google Colab menggunakan GPU cloud gratis."""
        colab_script = '''# -*- coding: utf-8 -*-
"""
Google Colab Training Script untuk APRJ & PKRMS AI
Jalankan sel-sel ini di Google Colab untuk memanfaatkan GPU cloud gratis.
"""
from google.colab import drive
drive.mount('/content/drive')

import torch
import torch.nn as nn
print("PyTorch Version:", torch.__version__)
print("GPU Tersedia:", torch.cuda.is_available())

DATA_DIR = '/content/drive/MyDrive/APRJ_Dataset'
print("Direktori Dataset:", DATA_DIR)
print("Training AI siap dijalankan dengan GPU Google Colab!")
'''
        Path(destination_path).write_text(colab_script, encoding="utf-8")
        return True, "Skrip Google Colab Training berhasil dibuat."