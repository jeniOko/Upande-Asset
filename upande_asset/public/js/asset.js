// Asset Insurance additions on the ERPNext Asset form.
frappe.ui.form.on("Asset", {
	setup(frm) {
		frm.set_df_property("insurance_policy", "read_only", 1);
	},

	asset_category(frm) {
		// Copy explicitly: fetch_from treats a 0 Check as empty and would not overwrite.
		if (!frm.is_new() || !frm.doc.asset_category) return;
		frappe.db.get_value("Asset Category", frm.doc.asset_category, "insurance_required").then((r) => {
			frm.set_value("insurance_required", cint(r.message && r.message.insurance_required));
		});
	},

	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 1) return;
		show_insurance_headline(frm);

		// QR sticker (Asset QR Label print format): scanning it opens the mobile check sheet for this asset.
		frm.add_custom_button(__("Print QR Label"), () => {
			const params = new URLSearchParams({ doctype: "Asset", name: frm.doc.name, format: "Asset QR Label", no_letterhead: 1 });
			window.open(`/printview?${params}`, "_blank");
		});

		if (frm.doc.insurance_required && !["Scrapped", "Sold"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Insurance Claim"), () => {
				frappe.new_doc("Insurance Claim", {
					asset: frm.doc.name,
					company: frm.doc.company,
				});
			}, __("Create"));
		}
	},
});

function show_insurance_headline(frm) {
	if (!frm.doc.insurance_required) return;
	frappe.xcall("upande_asset.upande_asset.insurance.asset.get_asset_insurance_status", { asset: frm.doc.name })
		.then((s) => {
			if (!s || !s.required) return;
			const removal = (s.requests || []).find((q) => q.endorsement_type === "Remove Assets");
			if (s.state === "Cover requested") {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator blue">${__("Cover requested")}</span>
						${__("Adding this asset to policy {0} is waiting for the insurer's confirmation.", [s.requests[0].policy])}
					</div></div>`
				);
				return;
			}
			if (removal) {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator orange">${__("Removal requested")}</span>
						${__("Removing this asset from policy {0} is waiting for the insurer's confirmation; it stays covered until then.", [removal.policy])}
					</div></div>`
				);
				return;
			}
			if (s.state === "Uninsured") {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator red">${__("Uninsured")}</span>
						${__("This asset requires insurance but is not on any active Insurance Policy.")}
					</div></div>`
				);
			} else if (s.state === "Cover expiring") {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator orange">${__("Cover expiring")}</span>
						${__("Insurance cover ends in {0} day(s).", [s.days_left])}
					</div></div>`
				);
			} else if (s.state === "Renewal in progress") {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator purple">${__("Renewal in progress")}</span>
						${__("Cover ends in {0} day(s); its renewal is under way but not complete.", [s.days_left])}
					</div></div>`
				);
			} else if (s.state === "Awaiting premium") {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator yellow">${__("Awaiting premium")}</span>
						${__("Covered by a renewed policy whose premium invoice is not settled yet.")}
					</div></div>`
				);
			}
		});
}
