"""UPI QR for the registration workflow. Showing a QR never marks anything as paid."""
from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import qrcode
from sqlalchemy.orm import Session

from config.settings import get_settings
from services import settings_service
from services.errors import BusinessRuleError, ValidationError

QR_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}


@dataclass(frozen=True)
class UpiQr:
    png: bytes
    dynamic: bool  # True: generated with the amount pre-filled; False: owner's uploaded image
    upi_id: str
    payee: str


def build_upi_uri(upi_id: str, payee: str, amount: Decimal | None = None, note: str = "") -> str:
    if "@" not in (upi_id or ""):
        raise ValidationError("UPI ID must look like name@bank.", "UPI ID")
    parts = [f"pa={quote(upi_id.strip(), safe='@')}", f"pn={quote(payee.strip() or 'Gym')}", "cu=INR"]
    if amount is not None:
        parts.append(f"am={Decimal(amount):.2f}")
    if note:
        parts.append(f"tn={quote(note[:60])}")
    return "upi://pay?" + "&".join(parts)


def render_qr_png(data: str) -> bytes:
    image = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def store_qr_image(source: Path | str) -> str:
    src = Path(source)
    if src.suffix.lower() not in QR_IMAGE_EXTENSIONS or not src.is_file():
        raise ValidationError("QR code must be a png, jpg or bmp image.", "UPI QR")
    folder = get_settings().data_dir / "images"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"upi_qr{src.suffix.lower()}"
    target.write_bytes(src.read_bytes())
    return f"images/{target.name}"


def get_payment_qr(session: Session, amount: Decimal, note: str = "") -> UpiQr:
    """The owner's configured QR: uploaded image first, else generated from their UPI ID."""
    upi_id = settings_service.get(session, "payment.upi_id")
    payee = settings_service.get(session, "payment.upi_payee_name") or settings_service.get(session, "gym.name")
    rel = settings_service.get(session, "payment.upi_qr_path")
    if rel:
        path = get_settings().data_dir / rel
        if path.is_file():
            return UpiQr(path.read_bytes(), False, upi_id, payee)
    if upi_id:
        return UpiQr(render_qr_png(build_upi_uri(upi_id, payee, amount, note)), True, upi_id, payee)
    raise BusinessRuleError("No UPI QR configured. Add your UPI QR image or UPI ID in Settings.")
