# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""doc_events for core doctypes that are not covered by premium.py / asset.py."""


def payment_entry_on_change(doc, method=None):
	from upande_asset.upande_asset.insurance.premium import payment_on_change

	payment_on_change(doc)
	if doc.get("insurance_claim"):
		from upande_asset.upande_asset.doctype.insurance_claim.insurance_claim import update_settlement

		update_settlement(doc.insurance_claim)
