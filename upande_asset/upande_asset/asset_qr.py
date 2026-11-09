# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Asset QR codes (the "Asset QR Label" print format and the mobile check sheet).

The code holds the address of the mobile check sheet for the asset
(…/checksheet?asset=<asset number>): pointing any phone camera at the label opens
the check for that asset. The scanner inside the page, and lookup_for_scan, also
accept a bare asset number (the first line of older labels).

Images are made on the fly (Frappe's own pyqrcode, as used for two-factor login),
so nothing has to be generated or uploaded per asset.
"""

import base64
import io
from urllib.parse import parse_qs, quote, urlparse

import frappe


def qr_target(asset):
	"""What the QR code holds: the mobile check sheet address for the asset."""
	return frappe.utils.get_url(f"/checksheet?asset={quote(asset)}")


def qr_png(asset, scale=8):
	import pyqrcode

	buf = io.BytesIO()
	# Medium error correction survives scuffed labels; quiet zone of 2 modules keeps it compact.
	pyqrcode.create(qr_target(asset), error="M").png(buf, scale=scale, quiet_zone=2)
	return buf.getvalue()


def asset_qr_data_uri(asset):
	"""Jinja (print formats): <img src="{{ asset_qr_data_uri(doc.name) }}">. A data URI so
	wkhtmltopdf needs no file or network access."""
	if not asset:
		return ""
	return "data:image/png;base64," + base64.b64encode(qr_png(asset)).decode()


def asset_from_scan(text):
	"""Asset number from whatever a scanner read: our check sheet address, or a bare number
	(the first line of older labels)."""
	text = (text or "").strip()
	if not text:
		return ""
	if "://" in text or text.startswith("/"):
		values = parse_qs(urlparse(text).query).get("asset")
		return (values[0] if values else "").strip()
	return text.replace("\r", "\n").split("\n")[0].strip()


@frappe.whitelist()
def get_qr(asset):
	"""The QR code as a PNG data URI (Asset form preview)."""
	frappe.has_permission("Asset", "read", asset, throw=True)
	return asset_qr_data_uri(asset)
