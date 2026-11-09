// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

const POLICY_API = "upande_asset.upande_asset.doctype.insurance_policy.insurance_policy";

frappe.ui.form.on("Insurance Policy", {
	setup(frm) {
		frm.set_query("policy_type", () => ({ filters: { enabled: 1 } }));
		frm.set_query("asset", "insured_assets", (doc) => ({
			query: `${POLICY_API}.get_insurable_assets`,
			filters: {
				company: doc.company,
				exclude: (doc.insured_assets || []).filter((r) => r.row_status === "Active" && r.asset).map((r) => r.asset),
			},
		}));
		frm.set_query("insurer", () => ({ filters: { disabled: 0 } }));
		frm.set_query("broker", () => ({ filters: { disabled: 0 } }));
		frm.set_query("opening_journal_entry", (doc) => ({ filters: { company: doc.company, docstatus: 1 } }));
		frm.set_query("cost_center", (doc) => ({ filters: { company: doc.company, is_group: 0 } }));
		// Address & Contact tab – same queries ERPNext uses on a Purchase Order.
		frm.set_query("supplier_address", (doc) => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Supplier", link_name: doc.insurer },
		}));
		frm.set_query("contact_person", (doc) => ({
			query: "frappe.contacts.doctype.contact.contact.contact_query",
			filters: { link_doctype: "Supplier", link_name: doc.insurer },
		}));
		["billing_address", "dispatch_address", "shipping_address"].forEach((f) => {
			frm.set_query(f, (doc) => ({
				query: "frappe.contacts.doctype.address.address.address_query",
				filters: { link_doctype: "Company", link_name: doc.company },
			}));
		});
	},


	insurance_for(frm) {
		if (frm.doc.insurance_for === "Other" && (frm.doc.insured_assets || []).length) {
			frappe.confirm(__("Non-asset policies do not list insured assets. Remove the {0} asset row(s)?", [frm.doc.insured_assets.length]),
				() => { frm.clear_table("insured_assets"); frm.refresh_field("insured_assets"); },
				() => frm.set_value("insurance_for", "Assets"));
		}
	},

	insurer(frm) {
		if (!frm.doc.insurer) {
			frm.set_value({ supplier_address: "", address_display: "", contact_person: "", contact_display: "", contact_mobile: "", contact_email: "" });
			return;
		}
		frappe.xcall(`${POLICY_API}.get_insurer_details`, { insurer: frm.doc.insurer }).then((r) => frm.set_value(r));
	},

	supplier_address: (frm) => set_address_display(frm, "supplier_address", "address_display"),
	dispatch_address: (frm) => set_address_display(frm, "dispatch_address", "dispatch_address_display"),
	shipping_address: (frm) => set_address_display(frm, "shipping_address", "shipping_address_display"),
	billing_address: (frm) => set_address_display(frm, "billing_address", "billing_address_display"),

	contact_person(frm) {
		if (!frm.doc.contact_person) {
			frm.set_value({ contact_display: "", contact_mobile: "", contact_email: "" });
			return;
		}
		frappe.db.get_value("Contact", frm.doc.contact_person, ["full_name", "mobile_no", "phone", "email_id"]).then((r) => {
			const c = r.message || {};
			frm.set_value({ contact_display: c.full_name, contact_mobile: c.mobile_no || c.phone, contact_email: c.email_id });
		});
	},

	tc_name(frm) {
		if (!frm.doc.tc_name) return;
		frappe.xcall("erpnext.setup.doctype.terms_and_conditions.terms_and_conditions.get_terms_and_conditions", {
			template_name: frm.doc.tc_name,
			doc: frm.doc,
		}).then((terms) => frm.set_value("terms", terms || ""));
	},

	basic_premium: (frm) => set_total(frm),
	levies: (frm) => set_total(frm),

	async refresh(frm) {
		if (frm.is_new()) return;
		const ctx = await frappe.xcall(`${POLICY_API}.get_policy_context`, { policy: frm.doc.name });
		const live = frm.doc.docstatus === 1 && ["Active", "Expiring", "Renewal In Progress", "Awaiting Premium"].includes(frm.doc.status);

		// ── Draft: new cover with no history ────────────────────────────────
		if (frm.doc.docstatus === 0 && ctx.is_accounts_user && !ctx.open_procurement) {
			frm.add_custom_button(__("Raise Purchase Request"), () => raise_procurement(frm, ctx, "raise_purchase_request"));
		}

		if (frm.doc.docstatus !== 1) return;

		// ── Premium invoice ────────────────────────────────────────────────
		if (frm.doc.is_opening !== "Yes" && !frm.doc.purchase_invoice && !ctx.premium_invoiced && !ctx.open_procurement
			&& ctx.is_accounts_user && frm.doc.status !== "Cancelled") {
			frm.add_custom_button(__("Make Premium Invoice"), () => {
				frappe.xcall(`${POLICY_API}.make_premium_invoice`, { policy: frm.doc.name }).then(open_unsaved);
			}, __("Create"));
		}
		if (ctx.draft_premium_pi) {
			frm.add_custom_button(__("Draft Premium Invoice"), () => frappe.set_route("Form", "Purchase Invoice", ctx.draft_premium_pi), __("View"));
		}

		// ── Renewal ─────────────────────────────────────────────────────────
		if (["Active", "Expiring", "Expired", "Awaiting Premium", "Renewal In Progress"].includes(frm.doc.status) && !frm.doc.renewed_to && !ctx.draft_renewal
			&& ctx.is_accounts_user) {
			frm.add_custom_button(__("Raise Renewal Request"), () => raise_procurement(frm, ctx, "raise_renewal_request"), __("Create"));
		}
		if (ctx.draft_renewal) {
			frm.add_custom_button(__("Draft Renewal"), () => frappe.set_route("Form", "Insurance Policy", ctx.draft_renewal), __("View"));
		}

		// ── Endorsements ───────────────────────────────────────────────────
		if (live && ctx.is_accounts_manager) {
			const actions = (frm.doc.insurance_for || "Assets") === "Assets"
				? ["Add Assets", "Remove Assets", "Change Sum Insured", "Cancel Policy"]
				: ["Change Sum Insured", "Cancel Policy"];
			actions.forEach((action) => {
				frm.add_custom_button(__(action), () => endorse_dialog(frm, ctx, action), __("Endorse"));
			});
		}
		const pending = ctx.pending_endorsements || [];
		if (ctx.is_accounts_manager && pending.length) {
			frm.add_custom_button(__("Confirm Endorsement"), () => confirm_dialog(frm, ctx), __("Endorse"));
			frm.add_custom_button(__("Withdraw Request"), () => withdraw_dialog(frm, ctx), __("Endorse"));
		}
		if (ctx.is_accounts_manager && (ctx.endorsements_without_document || []).length) {
			frm.add_custom_button(__("Raise Premium Document"), () => raise_endorsement_dialog(frm, ctx), __("Endorse"));
		}
		const alerts = [];
		if (pending.length) {
			alerts.push(__("Waiting for the insurer: {0}. Use Endorse → Confirm Endorsement when they reply.",
				[pending.map((e) => e.label).join("; ")]));
		}
		if ((ctx.endorsements_without_document || []).length) {
			alerts.push(__("No premium document yet for {0}. Use Endorse → Raise Premium Document.",
				[ctx.endorsements_without_document.map((e) => e.label).join("; ")]));
		}
		if (alerts.length) frm.dashboard.set_headline_alert(alerts.join("<br>"), pending.length ? "blue" : "orange");
		if (ctx.is_accounts_manager && (frm.doc.endorsements || []).some((e) => e.status === "Active")) {
			frm.add_custom_button(__("Reverse Endorsement"), () => reverse_dialog(frm), __("Endorse"));
		}

		// Deep link from Asset form / notifications.
		if (frappe.route_options && frappe.route_options.__endorse && live && ctx.is_accounts_manager) {
			const { __endorse, __asset } = frappe.route_options;
			frappe.route_options = null;
			endorse_dialog(frm, ctx, __endorse, __asset);
		}
	},
});

frappe.ui.form.on("Insurance Policy Asset", {
	asset(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.asset) return;
		frappe.db.get_value("Asset", row.asset, ["value_after_depreciation", "net_purchase_amount"]).then((r) => {
			const v = r.message || {};
			frappe.model.set_value(cdt, cdn, "book_value_at_inception", flt(v.value_after_depreciation));
			if (!row.sum_insured) {
				frappe.model.set_value(cdt, cdn, "sum_insured", flt(v.value_after_depreciation) || flt(v.net_purchase_amount));
			}
		});
	},
	sum_insured: (frm) => set_sum_insured(frm),
	insured_assets_remove: (frm) => set_sum_insured(frm),
});

function set_address_display(frm, link_field, display_field) {
	if (!frm.doc[link_field]) {
		frm.set_value(display_field, "");
		return;
	}
	frappe.xcall("frappe.contacts.doctype.address.address.get_address_display", { address_dict: frm.doc[link_field] })
		.then((html) => frm.set_value(display_field, html || ""));
}

function set_total(frm) {
	frm.set_value("total_premium", flt(frm.doc.basic_premium) + flt(frm.doc.levies));
}

function set_sum_insured(frm) {
	const total = (frm.doc.insured_assets || [])
		.filter((r) => r.row_status !== "Removed")
		.reduce((s, r) => s + flt(r.sum_insured), 0);
	frm.set_value("total_sum_insured", total);
}

// Documents come back unsaved and open as a new form (like ERPNext's "Create →" buttons):
// the user completes any site-specific mandatory fields and saves it.
function open_unsaved(doc) {
	frappe.model.sync(doc);
	frappe.set_route("Form", doc.doctype, doc.name);
}

function with_procurement(ctx, callback) {
	if (ctx.renewal_procurement !== "Ask") return callback(ctx.renewal_procurement);
	frappe.prompt(
		{ fieldname: "procurement", fieldtype: "Select", label: __("Procure via"), options: "Material Request\nPurchase Order", reqd: 1, default: "Material Request" },
		(v) => callback(v.procurement),
		__("Procurement"),
		__("Continue")
	);
}

function raise_procurement(frm, ctx, method) {
	with_procurement(ctx, (procurement) => {
		frappe.xcall(`${POLICY_API}.${method}`, { policy: frm.doc.name, procurement }).then((r) => {
			let msg = __("Review and save the {0} to request the premium.", [__(r.doc.doctype)]);
			if (r.policy && r.policy !== frm.doc.name) msg = __("Renewal policy {0} created.", [r.policy]) + " " + msg;
			if (r.excluded && r.excluded.length) {
				msg += " " + __("Excluded (scrapped / sold / no longer flagged): {0}", [r.excluded.join(", ")]);
			}
			frappe.show_alert({ message: msg, indicator: "green" }, 10);
			open_unsaved(r.doc);
		});
	});
}

function open_endorsement_document(frm, ctx, group, total) {
	const run = (procurement) => frappe.xcall(`${POLICY_API}.make_endorsement_document`,
		{ policy: frm.doc.name, endorsement_group: group, procurement }).then(open_unsaved);
	// Returned premium is a debit note; extra premium goes through procurement.
	if (flt(total) < 0) return run(null);
	with_procurement(ctx, run);
}

function pending_options(frm, ctx) {
	const currency = erpnext.get_currency(frm.doc.company);
	return (ctx.pending_endorsements || []).map((e) => ({
		value: e.group,
		label: `${e.label} (${__("requested for {0}", [frappe.datetime.str_to_user(e.effective_date)])}, ${format_currency(e.total, currency)})`,
	}));
}

// The insurer has endorsed the policy: their number, effective date and premium; the change is applied.
function confirm_dialog(frm, ctx) {
	const pending = ctx.pending_endorsements;
	const d = new frappe.ui.Dialog({
		title: __("Confirm Endorsement"),
		size: "large",
		fields: [
			{ fieldname: "group", fieldtype: "Select", label: __("Request"), reqd: 1, options: pending_options(frm, ctx),
				default: pending[0].group, onchange: () => fill() },
			{ fieldtype: "Section Break", label: __("From the insurer") },
			{ fieldname: "endorsement_no", fieldtype: "Data", label: __("Insurer's Endorsement No"), reqd: 1 },
			{ fieldname: "effective_date", fieldtype: "Date", label: __("Effective Date"), reqd: 1 },
			{ fieldtype: "Column Break" },
			{ fieldname: "premium_adjustment", fieldtype: "Currency", label: __("Premium Charged (+) / Returned (−)"),
				description: __("The insurer's figure. Extra premium is billed through the Material Request / Purchase Order; a refund is recorded with a Journal Entry.") },
			{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			{ fieldname: "open_refund", fieldtype: "Check", label: __("Record the refund now (Journal Entry)"), default: 1,
				depends_on: "eval:flt(doc.premium_adjustment) < 0" },
		],
		primary_action_label: __("Confirm"),
		primary_action(v) {
			frappe.xcall(`${POLICY_API}.confirm_endorsement`, { policy: frm.doc.name, endorsement_group: v.group, payload: {
				endorsement_no: v.endorsement_no, effective_date: v.effective_date, premium_adjustment: v.premium_adjustment, remarks: v.remarks,
			} }).then((r) => {
				d.hide();
				frappe.show_alert({ message: __("Endorsement {0} applied to the policy.", [v.endorsement_no]), indicator: "green" });
				if (flt(r.total) < 0 && v.open_refund) {
					open_endorsement_document(frm, ctx, r.endorsement_group, r.total);
				} else {
					frm.reload_doc();
				}
			});
		},
	});
	const fill = () => {
		const e = pending.find((x) => x.group === d.get_value("group"));
		d.set_values({ effective_date: e.effective_date, premium_adjustment: e.total });
	};
	d.show();
	fill();
}

function withdraw_dialog(frm, ctx) {
	frappe.prompt([
		{ fieldname: "group", fieldtype: "Select", label: __("Request"), reqd: 1, options: pending_options(frm, ctx) },
		{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason") },
	], (v) => frappe.xcall(`${POLICY_API}.withdraw_endorsement`, { policy: frm.doc.name, endorsement_group: v.group, reason: v.reason })
		.then(() => { frappe.show_alert({ message: __("Request withdrawn"), indicator: "green" }); frm.reload_doc(); }),
	__("Withdraw Request"), __("Withdraw"));
}

function raise_endorsement_dialog(frm, ctx) {
	const rows = ctx.endorsements_without_document;
	const currency = erpnext.get_currency(frm.doc.company);
	frappe.prompt(
		{ fieldname: "group", fieldtype: "Select", label: __("Endorsement"), reqd: 1,
			options: rows.map((e) => ({ value: e.group, label: `${e.label} (${format_currency(e.total, currency)})` })) },
		(v) => open_endorsement_document(frm, ctx, v.group, rows.find((e) => e.group === v.group).total),
		__("Raise Premium Document"),
		__("Open")
	);
}

function endorse_dialog(frm, ctx, action, preset_asset) {
	const active = (frm.doc.insured_assets || []).filter((r) => r.row_status === "Active");
	// Adding assets / changing their sums: a suggested premium per asset (shown to procurement line by line).
	const per_asset = action === "Add Assets" || (action === "Change Sum Insured" && (frm.doc.insurance_for || "Assets") === "Assets");
	const fields = [
		{ fieldname: "effective_date", fieldtype: "Date", label: __("Requested Effective Date"), reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "endorsement_no", fieldtype: "Data", label: __("Insurer's Endorsement No"),
			description: __("Leave empty to send this as a request; the policy changes once the insurer confirms it. Fill it in only if the insurer has already endorsed the policy.") },
		{ fieldtype: "Column Break" },
		{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
		{ fieldtype: "Section Break" },
	];
	if (action === "Add Assets") {
		fields.push({
			fieldname: "assets", fieldtype: "Table", label: __("Assets"), in_place_edit: true, reqd: 1,
			fields: [
				{ fieldname: "asset", fieldtype: "Link", options: "Asset", label: __("Asset"), in_list_view: 1, reqd: 1,
					get_query: () => ({ query: `${POLICY_API}.get_insurable_assets`, filters: { company: frm.doc.company, exclude: active.map((r) => r.asset) } }) },
				{ fieldname: "sum_insured", fieldtype: "Currency", label: __("Sum Insured"), in_list_view: 1, reqd: 1 },
				{ fieldname: "premium", fieldtype: "Currency", label: __("Suggested Premium"), in_list_view: 1,
					description: __("Pro-rata figure from Calculate; change it to what you expect the insurer to charge.") },
			],
			description: __("Add each asset with its sum insured, then press Calculate for the suggested premium per asset."),
			data: preset_asset ? [{ asset: preset_asset }] : [],
		});
	} else if (action === "Remove Assets") {
		fields.push({
			fieldname: "assets", fieldtype: "Table", label: __("Assets to remove"), cannot_add_rows: true, in_place_edit: true,
			fields: [
				{ fieldname: "select", fieldtype: "Check", label: __("Remove"), in_list_view: 1, columns: 1 },
				{ fieldname: "asset", fieldtype: "Link", options: "Asset", label: __("Asset"), in_list_view: 1, read_only: 1 },
				{ fieldname: "sum_insured", fieldtype: "Currency", label: __("Sum Insured"), in_list_view: 1, read_only: 1 },
			],
			data: active.map((r) => ({ select: r.asset === preset_asset ? 1 : 0, asset: r.asset, sum_insured: r.sum_insured })),
		});
	} else if (action === "Change Sum Insured" && (frm.doc.insurance_for || "Assets") !== "Assets") {
		fields.push(
			{ fieldname: "current_sum_insured", fieldtype: "Currency", label: __("Current Sum Insured"), read_only: 1, default: frm.doc.total_sum_insured },
			{ fieldtype: "Column Break" },
			{ fieldname: "new_sum_insured", fieldtype: "Currency", label: __("New Sum Insured"), reqd: 1, default: frm.doc.total_sum_insured },
		);
	} else if (action === "Change Sum Insured") {
		fields.push({
			fieldname: "assets", fieldtype: "Table", label: __("Assets"), cannot_add_rows: true, in_place_edit: true,
			fields: [
				{ fieldname: "asset", fieldtype: "Link", options: "Asset", label: __("Asset"), in_list_view: 1, read_only: 1 },
				{ fieldname: "sum_insured", fieldtype: "Currency", label: __("Current"), in_list_view: 1, read_only: 1 },
				{ fieldname: "new_sum_insured", fieldtype: "Currency", label: __("New Sum Insured"), in_list_view: 1 },
				{ fieldname: "premium", fieldtype: "Currency", label: __("Suggested Premium"), in_list_view: 1,
					description: __("Pro-rata figure from Calculate; change it to what you expect the insurer to charge.") },
			],
			description: __("Change the sums insured, then press Calculate for the suggested premium per asset."),
			data: active.map((r) => ({ asset: r.asset, sum_insured: r.sum_insured, new_sum_insured: r.sum_insured })),
		});
	} else {
		fields.push({ fieldtype: "HTML", options: `<p class="text-muted">${__("All {0} active asset row(s) will be removed and the policy marked Cancelled. The insurer's refund of unexpired premium is recorded with a Journal Entry once they confirm.", [active.length])}</p>` });
	}
	fields.push(
		{ fieldtype: "Section Break", label: __("Premium") },
		{ fieldname: "computed", fieldtype: "Currency", label: per_asset ? __("Total Suggested Premium") : __("Estimated Premium (pro-rata)"), read_only: 1 },
		{ fieldname: "basis", fieldtype: "HTML" },
		{ fieldtype: "Column Break" },
		{ fieldname: "premium_adjustment", fieldtype: "Currency", label: __("Expected Premium"),
			hidden: per_asset ? 1 : 0,
			description: __("Leave blank to use the pro-rata estimate. Negative = premium returned. The insurer's figure is entered when they confirm.") },
		{ fieldname: "create_premium_document", fieldtype: "Check", label: __("Ask procurement for the extra premium"), default: 1,
			description: __("Opens a Material Request or Purchase Order (unsaved) so procurement can take the change to the insurer.") },
	);

	const d = new frappe.ui.Dialog({
		title: __("Endorsement Request: {0}", [__(action)]),
		size: "large",
		fields,
		primary_action_label: __("Send Request"),
		primary_action(values) {
			const payload = build_payload(action, values);
			frappe.xcall(`${POLICY_API}.endorse`, { policy: frm.doc.name, action, payload }).then((r) => {
				d.hide();
				frappe.show_alert({
					message: r.status === "Requested"
						? __("Request recorded. Confirm it with the insurer's endorsement number when they reply.")
						: __("Endorsement recorded."),
					indicator: "green",
				}, 8);
				// Extra premium: procurement takes the request to the insurer. Returned premium: once confirmed.
				const raise = payload.create_premium_document && flt(r.total) && (flt(r.total) > 0 || r.status === "Active");
				raise ? open_endorsement_document(frm, ctx, r.endorsement_group, r.total) : frm.reload_doc();
			});
		},
	});
	d.add_custom_action(__("Calculate"), () => preview(frm, d, action, true));
	if (per_asset) {
		// Edited suggestions update the total straight away.
		d.fields_dict.assets.grid.wrapper.on("change", "input", () => setTimeout(() => sum_suggested(d), 50));
	}
	["effective_date", "new_sum_insured"].forEach((f) => {
		if (d.fields_dict[f]) d.fields_dict[f].df.onchange = () => preview(frm, d, action, f === "effective_date");
	});
	d.show();
	if (action === "Cancel Policy" || preset_asset) preview(frm, d, action);
}

function build_payload(action, values) {
	let assets = (values.assets || []).map((r) => ({ ...r }));
	if (action === "Remove Assets") assets = assets.filter((r) => r.select);
	if (action === "Change Sum Insured") assets = assets.filter((r) => flt(r.new_sum_insured) !== flt(r.sum_insured));
	return {
		endorsement_no: values.endorsement_no,
		effective_date: values.effective_date,
		remarks: values.remarks,
		assets,
		new_sum_insured: values.new_sum_insured,
		premium_adjustment: values.premium_adjustment === undefined || values.premium_adjustment === null || values.premium_adjustment === "" ? null : values.premium_adjustment,
		create_premium_document: values.create_premium_document ? 1 : 0,
	};
}

function sum_suggested(d) {
	const rows = d.fields_dict.assets ? d.fields_dict.assets.grid.get_data() || [] : [];
	if (rows.some((r) => r.premium !== undefined && r.premium !== null && r.premium !== "")) {
		d.set_value("computed", rows.reduce((s, r) => s + flt(r.premium), 0));
	}
}

// fill: put the pro-rata premium into each asset row (Calculate, or a changed effective date).
function preview(frm, d, action, fill) {
	const values = d.get_values(true) || {};
	const payload = build_payload(action, values);
	(payload.assets || []).forEach((a) => delete a.premium);  // the pro-rata figures, not the suggestions
	const policy_level = (frm.doc.insurance_for || "Assets") !== "Assets";
	if (action !== "Cancel Policy" && !policy_level && !(payload.assets || []).some((a) => a.asset)) return;
	frappe.xcall(`${POLICY_API}.get_endorsement_preview`, { policy: frm.doc.name, action, payload }).then((p) => {
		d.set_value("computed", p.total);
		const grid = d.fields_dict.assets && d.fields_dict.assets.grid;
		if (grid && grid.df.fields.some((f) => f.fieldname === "premium")) {
			const by_asset = {};
			(p.lines || []).forEach((ln) => { by_asset[ln.asset] = ln.amount; });
			(grid.get_data() || []).forEach((r) => {
				if (r.asset in by_asset && (fill || r.premium === undefined || r.premium === null || r.premium === "")) {
					r.premium = by_asset[r.asset];
				}
			});
			grid.refresh();
			sum_suggested(d);
		}
		d.fields_dict.basis.$wrapper.html(
			`<div class="text-muted small">${__("{0} of {1} policy days remaining from the effective date.", [p.remaining_days, p.policy_days])}</div>`
		);
	});
}

function reverse_dialog(frm) {
	const groups = {};
	(frm.doc.endorsements || []).filter((e) => e.status === "Active").forEach((e) => {
		const g = (groups[e.endorsement_group] = groups[e.endorsement_group] || { ...e, assets: [], total: 0 });
		g.assets.push(e.asset);
		g.total += flt(e.premium_adjustment);
	});
	const options = Object.values(groups).map((g) => ({
		value: g.endorsement_group,
		label: `${g.endorsement_no || __("No number")} – ${__(g.endorsement_type)} – ${g.assets.join(", ")} (${format_currency(g.total, erpnext.get_currency(frm.doc.company))})${g.reference_name ? " · " + g.reference_name : ""}`,
	}));
	frappe.prompt(
		{ fieldname: "group", fieldtype: "Select", label: __("Endorsement"), options, reqd: 1,
			description: __("Allowed only while its premium documents are draft or cancelled. Draft ones are deleted.") },
		(v) => frappe.xcall(`${POLICY_API}.reverse_endorsement`, { policy: frm.doc.name, endorsement_group: v.group })
			.then(() => { frappe.show_alert({ message: __("Endorsement reversed"), indicator: "green" }); frm.reload_doc(); }),
		__("Reverse Endorsement"),
		__("Reverse")
	);
}
