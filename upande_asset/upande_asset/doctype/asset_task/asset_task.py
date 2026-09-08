# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Asset Task - a day's planned work for an operable asset.

Which asset categories get a day plan, and whether they read an hour meter or an
odometer, is configured on Asset Category (custom_track_tasks / custom_meter_type)
rather than hard-coded here, so each site decides for itself.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now


class AssetTask(Document):
	def before_save(self):
		self.set_defaults_from_asset()
		self.roll_up_blocks()
		self.compute_hours()
		self.stamp_completion()

	def set_defaults_from_asset(self):
		if not self.tractor:
			return
		asset = frappe.db.get_value(
			"Asset", self.tractor, ["location", "asset_category", "company"], as_dict=True
		)
		if not asset:
			return
		if not self.location:
			self.location = asset.location
		if not self.company:
			self.company = asset.company
		if not self.meter_type:
			self.meter_type = (
				frappe.db.get_value("Asset Category", asset.asset_category, "custom_meter_type")
				or "Hour Meter"
			)

	def roll_up_blocks(self):
		if not self.blocks:
			return
		actual = sum(flt(b.actual_qty) for b in self.blocks)
		if actual:
			self.actual_qty = actual
		planned = sum(flt(b.planned_qty) for b in self.blocks)
		if planned and not flt(self.planned_qty):
			self.planned_qty = planned

	def compute_hours(self):
		start, end = flt(self.hour_meter_start), flt(self.hour_meter_end)
		if start and end and end >= start:
			self.actual_hours = round(end - start, 2)

	def stamp_completion(self):
		if self.status == "Completed":
			if not self.completed_at:
				self.completed_at = now()
				self.completed_by = frappe.session.user
		else:
			self.completed_at = None
			self.completed_by = None
