# -*- coding: utf-8 -*-
"""Training dan inferensi lokal untuk APRJ. Tidak menghasilkan label palsu."""
from __future__ import annotations

import math
import random
import shutil
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    nn = None
    F = None
    TORCH_AVAILABLE = False


def _require_torch():
    if not TORCH_AVAILABLE:
        raise RuntimeError("PyTorch belum terpasang. Jalankan build_portable.bat atau instal PyTorch.")


def _make_model(task_classes, width_count):
    """Gunakan satu definisi arsitektur agar training dan inferensi kompatibel."""
    _require_torch()
    from aprj_ai_model import SurveyMultiTaskNet, SURVEY_WIDTH_FIELDS as MODEL_WIDTH_FIELDS

    if int(width_count) != len(MODEL_WIDTH_FIELDS):
        raise ValueError(
            "Jumlah field lebar tidak cocok dengan arsitektur model utama "
            f"({width_count} diterima; {len(MODEL_WIDTH_FIELDS)} diperlukan)."
        )
    return SurveyMultiTaskNet(task_classes)


def _image_tensor(path, image_size):
    with Image.open(path) as source:
        image = source.convert("RGB").resize((image_size, image_size), Image.Resampling.BILINEAR)
        pixels = torch.frombuffer(bytearray(image.tobytes()), dtype=torch.uint8)
    pixels = pixels.reshape(image_size, image_size, 3).permute(2, 0, 1).float().div_(255.0)
    mean = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32).view(3, 1, 1)
    return (pixels - mean) / std


def _numeric(value):
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def train_model(records, task_options, width_fields, image_size, checkpoint_path,
                run_dir, epochs=10, batch_size=4, device_name="Otomatis",
                cancel_event=None, progress_callback=None, seed=42,
                training_mode="Latih ulang dari awal"):
    """Latih CNN multi-task dari label survei yang telah dikonfirmasi manusia."""
    _require_torch()
    started = time.time()
    epochs, batch_size = int(epochs), int(batch_size)
    if epochs < 1 or epochs > 500:
        raise ValueError("Epoch harus antara 1 dan 500.")
    if batch_size < 1 or batch_size > 256:
        raise ValueError("Batch harus antara 1 dan 256.")

    valid_records = []
    for record in records:
        image_path = Path(record.get("absolute_image_path", ""))
        labels = record.get("labels", {})
        if image_path.is_file() and isinstance(labels, dict):
            valid_records.append({"path": str(image_path), "labels": labels})
    if len(valid_records) < 2:
        raise ValueError("Diperlukan minimal 2 foto berlabel dengan file yang tersedia.")

    task_classes = {}
    for task, options in task_options.items():
        observed = {
            str(record["labels"].get(task, "")).strip()
            for record in valid_records
            if str(record["labels"].get(task, "")).strip() in options
        }
        ordered = [value for value in options if value in observed]
        if len(ordered) >= 2:
            task_classes[task] = ordered

    width_observed = {
        field: sum(_numeric(record["labels"].get(field)) is not None for record in valid_records)
        for field in width_fields
    }
    usable_widths = any(count >= 2 for count in width_observed.values())
    if not task_classes and not usable_widths:
        raise ValueError(
            "Label belum cukup untuk training. Setidaknya satu tugas klasifikasi harus "
            "memiliki 2 kelas berbeda, atau data lebar harus memiliki minimal 2 nilai numerik."
        )

    usable = []
    for record in valid_records:
        has_class = any(record["labels"].get(task) in classes for task, classes in task_classes.items())
        has_width = any(_numeric(record["labels"].get(field)) is not None for field in width_fields)
        if has_class or has_width:
            usable.append(record)
    if len(usable) < 2:
        raise ValueError("Kurang dari 2 foto memiliki label yang dapat dilatih.")

    random.Random(seed).shuffle(usable)
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if len(usable) >= 5:
        validation_count = max(1, int(round(len(usable) * 0.2)))
        validation = usable[:validation_count]
        training = usable[validation_count:]
    else:
        training, validation = usable, []

    if device_name == "CPU":
        device = torch.device("cpu")
    elif device_name == "GPU (CUDA)":
        if not torch.cuda.is_available():
            raise RuntimeError("GPU CUDA dipilih, tetapi CUDA tidak tersedia.")
        device = torch.device("cuda")
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = _make_model(task_classes, len(width_fields)).to(device)
    existing_checkpoint = Path(checkpoint_path)
    if training_mode == "Lanjutkan dari model aktif":
        if not existing_checkpoint.is_file():
            raise FileNotFoundError(
                "Tidak ada model aktif untuk dilanjutkan. Pilih 'Latih ulang dari awal'."
            )
        try:
            try:
                previous = torch.load(existing_checkpoint, map_location=device, weights_only=True)
            except TypeError:
                previous = torch.load(existing_checkpoint, map_location=device)
        except Exception as error:
            raise RuntimeError(f"Model aktif tidak dapat dibaca: {error}") from error
        if previous.get("task_classes") != task_classes:
            raise ValueError(
                "Label/kelas dataset tidak sama dengan model aktif. "
                "Gunakan 'Latih ulang dari awal' atau lengkapi dataset agar kelasnya sama."
            )
        if previous.get("width_fields") != list(width_fields):
            raise ValueError("Daftar field lebar tidak kompatibel dengan model aktif.")
        try:
            model.load_state_dict(previous["state_dict"])
        except Exception as error:
            raise RuntimeError(f"Bobot model aktif tidak kompatibel: {error}") from error
    elif training_mode != "Latih ulang dari awal":
        raise ValueError(f"Mode training tidak dikenal: {training_mode}")
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.0001)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    history = []
    best_loss = float("inf")
    best_validation_loss = float("inf")
    best_state = None
    width_ranges = {field: (0.0, 30.0) for field in width_fields}

    def run_epoch(items, training_mode):
        model.train(training_mode)
        random_items = list(items)
        if training_mode:
            random.shuffle(random_items)
        total_loss = 0.0
        batches = 0
        correct = {task: [0, 0] for task in task_classes}
        with torch.set_grad_enabled(training_mode):
            for offset in range(0, len(random_items), batch_size):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Training dibatalkan pengguna.")
                batch = random_items[offset:offset + batch_size]
                images, batch_labels = [], []
                for record in batch:
                    try:
                        images.append(_image_tensor(record["path"], image_size))
                        batch_labels.append(record["labels"])
                    except Exception:
                        continue
                if not images:
                    continue
                x = torch.stack(images).to(device)
                outputs = model(x)
                losses = []

                for task, classes in task_classes.items():
                    indices = [classes.index(str(labels.get(task)).strip())
                               if str(labels.get(task, "")).strip() in classes else -1
                               for labels in batch_labels]
                    mask = torch.tensor([idx >= 0 for idx in indices], dtype=torch.bool, device=device)
                    if mask.any():
                        y = torch.tensor([max(0, idx) for idx in indices], dtype=torch.long, device=device)
                        losses.append(F.cross_entropy(outputs["classes"][task][mask], y[mask]))
                        if not training_mode:
                            predicted = outputs["classes"][task][mask].argmax(dim=1)
                            pair = correct[task]
                            pair[0] += int((predicted == y[mask]).sum().item())
                            pair[1] += int(mask.sum().item())

                width_targets = []
                width_masks = []
                for field in width_fields:
                    values = [_numeric(labels.get(field)) for labels in batch_labels]
                    width_targets.append([value if value is not None else 0.0 for value in values])
                    width_masks.append([value is not None for value in values])
                if width_fields:
                    target = torch.tensor(width_targets, dtype=torch.float32, device=device).T
                    mask = torch.tensor(width_masks, dtype=torch.bool, device=device).T
                    if mask.any():
                        losses.append(F.smooth_l1_loss(outputs["widths"][mask], target[mask]))

                if not losses:
                    continue
                loss = sum(losses) / len(losses)
                if training_mode:
                    optimizer.zero_grad(set_to_none=True)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                    optimizer.step()
                total_loss += float(loss.detach().item())
                batches += 1
        mean_loss = total_loss / batches if batches else None
        accuracies = {
            task: (correct[task][0] / correct[task][1] if correct[task][1] else None)
            for task in task_classes
        }
        return mean_loss, accuracies

    for epoch in range(1, epochs + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Training dibatalkan pengguna.")
        train_loss, _ = run_epoch(training, True)
        val_loss, val_accuracy = run_epoch(validation, False) if validation else (None, {})
        score = val_loss if val_loss is not None else train_loss
        if score is None:
            raise RuntimeError("Tidak ada batch yang memiliki label valid untuk dihitung.")
        if val_loss is not None and val_loss < best_validation_loss:
            best_validation_loss = val_loss
        if score < best_loss:
            best_loss = score
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        entry = {
            "epoch": epoch, "train_loss": train_loss,
            "validation_loss": val_loss, "validation_accuracy": val_accuracy,
        }
        history.append(entry)
        if progress_callback:
            progress_callback(
                f"Epoch {epoch}/{epochs} | loss train={train_loss:.4f}"
                + (f" | loss validasi={val_loss:.4f}" if val_loss is not None else " | validasi tidak tersedia")
            )

    if best_state is None:
        raise RuntimeError("Training tidak menghasilkan checkpoint yang valid.")
    model.load_state_dict(best_state)
    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "format_version": 1,
        "state_dict": model.state_dict(),
        "task_classes": task_classes,
        "width_fields": list(width_fields),
        "trained_width_fields": [field for field, count in width_observed.items() if count >= 2],
        "image_size": int(image_size),
        "history": history,
        "best_validation_loss": best_validation_loss if math.isfinite(best_validation_loss) else None,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "training_samples": len(training),
        "validation_samples": len(validation),
        "device": str(device),
        "model_note": (
            "CNN lokal; bobot dilanjutkan dari checkpoint aktif (optimizer diinisialisasi ulang)."
            if training_mode == "Lanjutkan dari model aktif"
            else "CNN lokal yang diinisialisasi dari awal; bukan model pretrained."
        ),
        "training_mode": training_mode,
    }
    temporary = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
    torch.save(checkpoint, temporary)
    if checkpoint_path.is_file():
        backup_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".bak")
        shutil.copy2(checkpoint_path, backup_path)
    temporary.replace(checkpoint_path)

    summary = {
        "checkpoint": str(checkpoint_path),
        "training_samples": len(training),
        "validation_samples": len(validation),
        "task_classes": task_classes,
        "best_validation_loss": best_validation_loss if math.isfinite(best_validation_loss) else None,
        "elapsed_seconds": round(time.time() - started, 2),
        "device": str(device),
        "history": history,
    }
    (run_dir / "training_summary.json").write_text(
        __import__("json").dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def load_model(checkpoint_path, device_name="Otomatis"):
    _require_torch()
    checkpoint_path = Path(checkpoint_path)
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"Checkpoint model tidak ditemukan: {checkpoint_path}")
    if device_name == "CPU":
        device = torch.device("cpu")
    elif device_name == "GPU (CUDA)":
        if not torch.cuda.is_available():
            raise RuntimeError("GPU CUDA dipilih, tetapi CUDA tidak tersedia.")
        device = torch.device("cuda")
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    try:
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=True)
    except TypeError:
        checkpoint = torch.load(checkpoint_path, map_location=device)
    task_classes = checkpoint.get("task_classes")
    width_fields = checkpoint.get("width_fields")
    if not isinstance(task_classes, dict) or not isinstance(width_fields, list):
        raise ValueError("Format checkpoint tidak dikenali; latih ulang model melalui APRJ.")
    model = _make_model(task_classes, len(width_fields))
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device)
    model.eval()
    return model, checkpoint, device


def predict_image(image_path, checkpoint_path, device_name="Otomatis"):
    """Prediksi kelas dan confidence untuk satu foto; lebar adalah estimasi kasar."""
    _require_torch()
    model, checkpoint, device = load_model(checkpoint_path, device_name)
    image_size = int(checkpoint.get("image_size", 224))
    x = _image_tensor(image_path, image_size).unsqueeze(0).to(device)
    with torch.no_grad():
        output = model(x)
    predictions = {}
    for task, logits in output["classes"].items():
        probabilities = torch.softmax(logits[0], dim=0)
        index = int(probabilities.argmax().item())
        classes = checkpoint["task_classes"][task]
        predictions[task] = {
            "label": classes[index],
            "confidence": float(probabilities[index].item()),
        }
    widths = {}
    trained_width_fields = set(checkpoint.get("trained_width_fields", []))
    for index, field in enumerate(checkpoint.get("width_fields", [])):
        if field in trained_width_fields:
            widths[field] = max(0.0, float(output["widths"][0, index].item()))
    return {"classes": predictions, "widths": widths, "device": str(device),
            "model_note": checkpoint.get("model_note", "")}
