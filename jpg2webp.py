# tools/jpg2webp.py
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple

try:
    from PIL import Image, ImageOps
except Exception as e:
    raise RuntimeError(
        "Pillow (PIL) が見つかりません。画像変換には Pillow が必要です。\n"
        "インストール例:  pip install --upgrade pillow"
    ) from e

import os

IMG_ROOT = Path("site/image")

def find_jpgs(root: Path = IMG_ROOT) -> List[Path]:
    """site/image 以下の .jpg/.jpeg を全て列挙（再帰）"""
    exts = {".jpg", ".jpeg"}
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]

def corresponding_webp(jpg_path: Path) -> Path:
    """同名 .webp のパスを返す"""
    return jpg_path.with_suffix(".webp")

def precheck_abort_if_any_webp_exists(jpgs: List[Path]) -> Tuple[bool, List[Path]]:
    """
    開始前チェック：
    - jpg ごとに同名 .webp が既に存在するかを確認
    - 1件でも存在したら（その一覧を返して）処理を開始しない
    """
    conflicts = []
    for jp in jpgs:
        wp = corresponding_webp(jp)
        if wp.exists():
            conflicts.append(wp)
    return (len(conflicts) == 0, conflicts)

def convert_one(jpg_path: Path, *, quality: int = 85) -> None:
    """
    jpg → webp 変換。成功したら jpg を削除。
    - EXIFの回転を実画素に適用
    - ロッシーWebP（quality=85, optimize=True, method=6）
    """
    webp_path = corresponding_webp(jpg_path)
    webp_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(jpg_path) as im:
        im = ImageOps.exif_transpose(im)
        # 透過は通常jpgに無いが、念のためモードを適切化
        if im.mode not in ("RGB", "RGBA"):
            try:
                im = im.convert("RGB")
            except Exception:
                pass

        im.save(
            webp_path,
            format="WEBP",
            quality=quality,
            method=6,     # 圧縮効率 0..6
            optimize=True # ハフマン最適化
        )

    # 書き出し成功後に .jpg を削除
    os.remove(jpg_path)

def convert_all_or_abort(*, root: Path = IMG_ROOT, quality: int = 85) -> Tuple[int, int]:
    """
    1) 事前に .webp 既存衝突をスキャン → あれば例外で中止
    2) すべての .jpg を .webp へ変換 → .jpg 削除
    戻り値: (対象件数, 成功件数)
    """
    jpgs = find_jpgs(root)
    ok, conflicts = precheck_abort_if_any_webp_exists(jpgs)
    if not ok:
        # 1件でも .webp が既に存在 → 中止
        raise RuntimeError(
            f"既存の .webp が見つかったため中止します（{len(conflicts)}件）\n"
            + "\n".join(str(p) for p in conflicts[:20] + (["..."] if len(conflicts) > 20 else []))
        )

    converted = 0
    for jp in jpgs:
        convert_one(jp, quality=quality)
        converted += 1

    return (len(jpgs), converted)