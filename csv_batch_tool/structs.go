package main

type Root struct {
	BuyerDetails         BuyerDetails     `json:"buyer_details,omitempty"`
	SellerDetails        SellerDetails    `json:"seller_details,omitempty"`
	Products             []Product        `json:"products,omitempty"`
	MinimumOrderQuantity Quantity         `json:"minimum_order_quantity,omitempty"`
	LeadTag              LeadTag          `json:"lead_tag,omitempty"`
	Payment              Payment          `json:"payment,omitempty"`
	CallBack             string           `json:"call_back,omitempty"`
	NextSteps            NextSteps        `json:"next_steps,omitempty"`
	Metadata             Metadata         `json:"metadata,omitempty"`
}

/* ---------- Buyer ---------- */

type BuyerDetails struct {
	BuyerName           string   `json:"buyer_name,omitempty"`
	BuyerMobileNumber   *int64   `json:"buyer_mobile_number,omitempty"`
	BuyerLocation       Location `json:"buyer_location,omitempty"`
}

type Location struct {
	City     string `json:"buyer_city,omitempty"`
	State    string `json:"buyer_state,omitempty"`
	Locality string `json:"buyer_locality,omitempty"`

	// seller fields reuse same struct
	SellerCity     string `json:"seller_city,omitempty"`
	SellerState    string `json:"seller_state,omitempty"`
	SellerLocality string `json:"seller_locality,omitempty"`
}

/* ---------- Seller ---------- */

type SellerDetails struct {
	SellerName         string   `json:"seller_name,omitempty"`
	SellerMobileNumber *int64   `json:"seller_mobile_number,omitempty"`
	SellerLocation     Location `json:"seller_location,omitempty"`
}

/* ---------- Product ---------- */

type Product struct {
	ProductName               string          `json:"product_name,omitempty"`
	InStock                   string          `json:"in_stock,omitempty"`
	IsBuyerInterested         bool            `json:"is_buyer_interested,omitempty"`
	MostSpecificCategory      Category        `json:"most_specific_category,omitempty"`
	RelatedCategories         []Category      `json:"related_categories,omitempty"`
	Price                     Price           `json:"price,omitempty"`
	QuantityRequired          Quantity        `json:"quantity_required,omitempty"`
	QuantityAcceptedBySeller bool            `json:"quantity_accepted_by_seller,omitempty"`
	Specifications            []Specification `json:"specifications,omitempty"`
}

type Category struct {
	Name   string `json:"name,omitempty"`
	Reason string `json:"reason,omitempty"`
}

/* ---------- Price ---------- */

type Price struct {
	Value                 int            `json:"value,omitempty"`
	Currency              string         `json:"currency,omitempty"`
	PriceUnit             string         `json:"price_unit,omitempty"`
	PriceReactionByBuyer  string         `json:"price_reaction_by_buyer,omitempty"`
	PriceNegotiationNotes string         `json:"price_negotiation_notes,omitempty"`
	GST                   GST            `json:"gst,omitempty"`
	DeliveryCharge        DeliveryCharge `json:"delivery_charge,omitempty"`
	OfferDetails          string         `json:"offer_details,omitempty"`
}

type GST struct {
	IsGSTIncluded bool   `json:"is_gst_included,omitempty"`
	Value         int    `json:"value,omitempty"`
	Details       string `json:"details,omitempty"`
}

type DeliveryCharge struct {
	IsIncluded bool   `json:"is_delivery_charge_included,omitempty"`
	Value      int    `json:"value,omitempty"`
	Currency   string `json:"currency,omitempty"`
	Details    string `json:"details,omitempty"`
}

/* ---------- Quantity ---------- */

type Quantity struct {
	Value int    `json:"value,omitempty"`
	Unit  string `json:"unit,omitempty"`
}

/* ---------- Specifications ---------- */

type Specification struct {
	Name            string `json:"name,omitempty"`
	Value           string `json:"value,omitempty"`
	Unit            string `json:"unit,omitempty"`
	BuyerRequested  bool   `json:"buyer_requested,omitempty"`
	SellerMentioned bool   `json:"seller_mentioned,omitempty"`
}

/* ---------- Lead Tag ---------- */

type LeadTag struct {
	DealReadiness       string   `json:"deal_readiness,omitempty"`
	DealReadinessReason string   `json:"deal_readiness_reason,omitempty"`
	DealBlockers        []string `json:"deal_blockers,omitempty"`
}


/* ---------- Payment ---------- */

type Payment struct {
	PaymentMode    string `json:"payment_mode,omitempty"`
	PaymentDetails string `json:"payment_details,omitempty"`
}

/* ---------- Next Steps ---------- */

type NextSteps struct {
	BuyerNextSteps  []string `json:"buyer_next_steps,omitempty"`
	SellerNextSteps []string `json:"seller_next_steps,omitempty"`
}

/* ---------- Metadata ---------- */

type Metadata struct {
	BuyerIntent         BuyerIntent       `json:"buyer_intent,omitempty"`
	IntendedApplication string            `json:"intended_application,omitempty"`
	BuyerConclusion     BuyerConclusion   `json:"buyer_conclusion,omitempty"`
	PrimaryLanguage     string            `json:"primary_language,omitempty"`
	AllLanguages        []string          `json:"all_languages,omitempty"`
	CallType            CallType          `json:"call_type,omitempty"`
	AdditionalDetails   AdditionalDetails `json:"additional_details,omitempty"`
	CallPurpose         string            `json:"call_purpose,omitempty"`
}


type CallType struct {
	Evidence Evidence `json:"evidence,omitempty"`
	Reason   string   `json:"reason,omitempty"`
	Type     string   `json:"type,omitempty"`
}

type Evidence struct {
	BuyerPersona  string   `json:"buyer_persona,omitempty"`
	QuantityScale string   `json:"quantity_scale,omitempty"`
	OrderType     string   `json:"order_type,omitempty"`
	RepeatBuyer   bool     `json:"repeat_buyer,omitempty"`
	Keywords      []string `json:"keywords,omitempty"`
}

type BuyerIntent struct {
	IntentLevel string `json:"intent_level,omitempty"`
	Narrative   string `json:"narrative,omitempty"`
	Reasoning   string `json:"reasoning,omitempty"`
}

type BuyerConclusion struct {
	Category        string `json:"category,omitempty"`
	ConclusionNotes string `json:"conclusion_notes,omitempty"`
}

type AdditionalDetails struct {
	BuyerQueries  []QueryDetail `json:"buyer_queries,omitempty"`
	SellerQueries []QueryDetail `json:"seller_queries,omitempty"`
}

type QueryDetail struct {
	Query    string `json:"query,omitempty"`
	Sequence int    `json:"sequence,omitempty"`
	Category string `json:"category,omitempty"`
}
