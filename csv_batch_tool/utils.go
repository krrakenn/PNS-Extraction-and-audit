package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	// "os"
	"strings"
	"time"
)

func extractSoundURL(url string) string {
	if strings.Contains(url, "knowlarity") {
		match := knowlarityRegex.FindStringSubmatch(url)
		if len(match) > 1 {
			url = match[1]
		}
	}
	return url
}

func downloadAudio(url string) ([]byte, float64, string, int, string, error, string) {

	t0 := time.Now()
	var resp *http.Response
	var err error
	var url_type string

	if strings.Contains(url, "airtel") {
		url = strings.ReplaceAll(url, " ", "+")
		url_type = "airtel"
		hmacKey, xDate, digest := setHeaders("")
		t0 = time.Now()
		req, reqErr := http.NewRequest("GET", url, nil)
		if reqErr != nil {
			return nil, 0, url_type, 0, "", fmt.Errorf("Failed to create HTTP request"), "NA"
		}
		req.Header.Add("Authorization", hmacKey)
		req.Header.Add("X-Date", xDate)
		req.Header.Add("Digest", digest)
		resp, err = httpClient.Do(req)
	} else {
		url_type = "knowlarity"
		resp, err = httpClient.Get(url)
	}
	apiTime := time.Since(t0).Seconds()
	if err != nil {
		return nil, apiTime, url_type, 0, "", fmt.Errorf("Issue in link, failed to download audio"), "NA"
	}
	defer resp.Body.Close()

	down_code := resp.StatusCode
	down_status := resp.Status
	last_modified := resp.Header.Get("Last-Modified")
	if last_modified == "" {
		last_modified = "NA"
	}

	contentType := strings.ToLower(resp.Header.Get("Content-Type"))

	if strings.Contains(contentType, "text") || strings.Contains(contentType, "json") {
		return nil, apiTime, url_type, down_code, down_status, fmt.Errorf("Not a MP3 file"), last_modified
	}

	audioBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, apiTime, url_type, down_code, down_status, fmt.Errorf("Error in reading audio data"), last_modified
	}

	if len(audioBytes) == 0 {
		return nil, apiTime, url_type, down_code, down_status, fmt.Errorf("Downloaded audio is empty"), last_modified
	}

	maxAudioSize := 20 * 1024 * 1024
	if len(audioBytes) > maxAudioSize {
		return nil, apiTime, url_type, down_code, down_status, fmt.Errorf("Audio file too large, max allowed 20 mb"), last_modified
	}

	return audioBytes, apiTime, url_type, down_code, down_status, nil, last_modified
}

func setHeaders(requestBody string) (string, string, string) {
	xDate := time.Now().UTC().Format("Mon, 02 Jan 2006 15:04:05 GMT")
	hash := sha256.Sum256([]byte(requestBody))
	digest := "SHA-256=" + base64.StdEncoding.EncodeToString(hash[:])
	signatureString := fmt.Sprintf("x-date: %s\ndigest: %s", xDate, digest)

	h := hmac.New(sha256.New, []byte(apiKey))
	h.Write([]byte(signatureString))
	signature := base64.StdEncoding.EncodeToString(h.Sum(nil))
	hmacKey := fmt.Sprintf(`hmac username="%s", algorithm="hmac-sha256", headers="x-date digest", signature="%s"`, appID, signature)
	return hmacKey, xDate, digest
}

func generate(modelToRun string, audioBytes []byte, categories []string, userDetails map[string]string) (string, string, int, int, error) {
	timeout_seconds := 300
	b64_audiobytes := base64.StdEncoding.EncodeToString(audioBytes)
	//fmt.Printf("Audio bytes length: %d, B64 length: %d\n", len(audioBytes), len(b64_audiobytes))

	maxRetries := 2

	var parsed map[string]interface{}
	str_err := "Gemini API error"

	for attempt := 1; attempt <= maxRetries; attempt++ {
		fmt.Printf("Attempt %d to call LLM...\n", attempt)
		var resp *http.Response
		var err error
		if attempt == maxRetries {
			resp, err, _ = llm_req(modelToRun, b64_audiobytes, timeout_seconds, 1, categories, userDetails)
		} else {
			resp, err, _ = llm_req(modelToRun, b64_audiobytes, timeout_seconds, 0, categories, userDetails)
		}
		if err != nil {
			fmt.Printf("LLM attempt %d failed: request error: %v\n", attempt, err)
			if attempt == maxRetries {
				str_err = fmt.Sprintf("LLM request error: %v", err)
			}
			continue
		} else {
			defer resp.Body.Close()
		}

		if resp.StatusCode != 200 {
			fmt.Printf("LLM response status error: %s (Code: %d)\n", resp.Status, resp.StatusCode)
			if attempt == maxRetries {
				str_err = fmt.Sprintf("LLM response status: %v", resp.Status)
			}
			continue
		}

		// 2. Read response
		respBytes, err := io.ReadAll(resp.Body)
		if err != nil {
			fmt.Printf("LLM attempt %d failed: response read error: %v\n", attempt, err)
			if attempt == maxRetries {
				str_err = fmt.Sprintf("Gemini response read error: %v", err)
			}
			continue
		}

		// 3. Decode response JSON
		parsed = make(map[string]interface{})
		if err := json.Unmarshal(respBytes, &parsed); err != nil {
			fmt.Printf("LLM attempt %d failed: response JSON unmarshal error: %v\n", attempt, err)
			if attempt == maxRetries {
				str_err = fmt.Sprintf("Gemini JSON unmarshal error: %v", err)
			}
			continue
		}

		// Extract usage
		var promptTokens, completionTokens int
		if usage, ok := parsed["usage"].(map[string]interface{}); ok {
			if pt, ok := usage["prompt_tokens"].(float64); ok {
				promptTokens = int(pt)
			}
			if ct, ok := usage["completion_tokens"].(float64); ok {
				completionTokens = int(ct)
			}
		}

		// 4. Check API error object
		if errObj, ok := parsed["error"].(map[string]interface{}); ok {
			msg, _ := errObj["message"].(string)
			fmt.Printf("LLM attempt %d failed: API error: %v\n", attempt, msg)
			if attempt == maxRetries {
				str_err = fmt.Sprintf("Gemini API response error: %v", msg)
			}
			continue
		}

		// 6. Extract assistant content
		var text string
		text, _, err = extractAssistantContent(parsed)
		if err != nil {
			fmt.Printf("LLM attempt %d failed: content extraction error: %v\n", attempt, err)
			if attempt == maxRetries {
				str_err = fmt.Sprintf("Content extraction error: %v", err)
			}
			continue
		}

		if text == "" {
			fmt.Printf("LLM attempt %d failed: empty content in response\n", attempt)
			if attempt == maxRetries {
				str_err = "Empty content in response"
			}
			continue
		}

		var cen_data Root
		// fmt.Println("\n--- LLM RAW JSON ---")
		// fmt.Println(text)
		// fmt.Println("--------------------")
		if err := json.Unmarshal([]byte(text), &cen_data); err != nil {
			fmt.Printf("LLM attempt %d failed: output JSON unmarshal error: %v\n", attempt, err)
			if attempt == maxRetries {
				str_err = fmt.Sprintf("Error unmarshaling cen_data: %v", err)
			}
			continue
		}

		cleanJSONBytes, _ := json.Marshal(cen_data)
		cleanJSON := string(cleanJSONBytes)

		targetProdsCount := 0
		primaryProduct := Product{}
		isRelaxedMode := false

		// 1. Try to find products with explicit buyer interest
		for _, p := range cen_data.Products {
			if p.IsBuyerInterested && strings.TrimSpace(p.ProductName) != "" {
				targetProdsCount++
				if primaryProduct.ProductName == "" {
					primaryProduct = p
				}
			}
		}

		// 2. Relaxed Mode: If no interested products found, consider all validly named products
		if targetProdsCount == 0 {
			isRelaxedMode = true
			for _, p := range cen_data.Products {
				if strings.TrimSpace(p.ProductName) != "" {
					targetProdsCount++
					if primaryProduct.ProductName == "" {
						primaryProduct = p
					}
				}
			}
		}

		langLower := strings.ToLower(cen_data.Metadata.PrimaryLanguage)
		isBuyerNumberEmpty := cen_data.BuyerDetails.BuyerMobileNumber == nil || *cen_data.BuyerDetails.BuyerMobileNumber == 0

		if targetProdsCount == 0 || primaryProduct.ProductName == "" {
			return "", cleanJSON, promptTokens, completionTokens, nil
		}

		if langLower != "hindi" && langLower != "english" {
			return "", cleanJSON, promptTokens, completionTokens, nil
		}
		if cen_data.Metadata.CallPurpose != "requirement" {
			return "", cleanJSON, promptTokens, completionTokens, nil
		}

		if cen_data.BuyerDetails.BuyerName == "" &&
			cen_data.BuyerDetails.BuyerLocation.City == "" && primaryProduct.QuantityRequired.Value == 0 &&
			isBuyerNumberEmpty && primaryProduct.Price.Value == 0 && len(cen_data.NextSteps.SellerNextSteps) == 0 {
			return "", cleanJSON, promptTokens, completionTokens, nil
		}

		productNames := []string{}
		for _, p := range cen_data.Products {
			if strings.TrimSpace(p.ProductName) != "" {
				productNames = append(productNames, strings.TrimSpace(p.ProductName))
			}
		}

		var sb strings.Builder

		// Title
		pName := CapitalizeFirst(primaryProduct.ProductName)
		if pName != "" {
			sb.WriteString(fmt.Sprintf("Call summary : %s\n\n", pName))
		}

		// 1. Buyer name and location
		var locationParts []string
		if cen_data.BuyerDetails.BuyerLocation.Locality != "" {
			locationParts = append(locationParts, CapitalizeFirst(cen_data.BuyerDetails.BuyerLocation.Locality))
		}
		if cen_data.BuyerDetails.BuyerLocation.City != "" {
			locationParts = append(locationParts, CapitalizeFirst(cen_data.BuyerDetails.BuyerLocation.City))
		}
		if cen_data.BuyerDetails.BuyerLocation.State != "" {
			locationParts = append(locationParts, CapitalizeFirst(cen_data.BuyerDetails.BuyerLocation.State))
		}

		buyerName := CapitalizeFirst(cen_data.BuyerDetails.BuyerName)
		locationStr := strings.Join(locationParts, ", ")

		var infoParts []string
		if locationStr != "" {
			if buyerName != "" {
				infoParts = append(infoParts, "Name: "+buyerName)
			}
			infoParts = append(infoParts, "Location : "+locationStr)
		}

		if len(infoParts) > 0 {
			sb.WriteString(strings.Join(infoParts, "  ") + "\n")
		}

		// 2. Buyer contact
		if !isBuyerNumberEmpty {
			sb.WriteString(fmt.Sprintf("Mobile / WhatsApp: %d\n\n", *cen_data.BuyerDetails.BuyerMobileNumber))
		} else {
			sb.WriteString("\n")
		}

		// 3. Products and Prices
		sb.WriteString("Product details:\n\n")

		// Check for uniform quantity (targetProdsCount is already calculated)
		allQtiesSame := true
		firstVal := primaryProduct.QuantityRequired.Value
		firstUnit := primaryProduct.QuantityRequired.Unit
		for _, p := range cen_data.Products {
			if !p.IsBuyerInterested && !isRelaxedMode {
				continue
			}
			if p.QuantityRequired.Value != firstVal || p.QuantityRequired.Unit != firstUnit {
				allQtiesSame = false
				break
			}
		}

		firstDisplayed := true
		for _, p := range cen_data.Products {
			if !p.IsBuyerInterested && !isRelaxedMode {
				continue
			}

			prodName := CapitalizeFirst(p.ProductName)
			if prodName == "" {
				continue
			}

			if firstDisplayed {
				prodName = "Needs " + prodName
				firstDisplayed = false
			}

			interestedSpecsCount := 0
			for _, s := range p.Specifications {
				if s.BuyerRequested {
					interestedSpecsCount++
				}
			}

			var specs []string
			for _, s := range p.Specifications {
				if s.BuyerRequested || interestedSpecsCount < 3 {
					specVal := s.Name
					if s.Value != "" {
						specVal += ": " + s.Value
					}
					if s.Unit != "" {
						specVal += " " + s.Unit
					}
					specs = append(specs, specVal)
				}
			}
			specStr := strings.Join(specs, ", ")

			qtyStr := ""
			// Only show per-product quantity if:
			// 1. It's the only product.
			// 2. There are multiple products with different quantities.
			if p.QuantityRequired.Value != 0 && (targetProdsCount == 1 || !allQtiesSame) {
				qtyStr = fmt.Sprintf("%v", p.QuantityRequired.Value)
				if p.QuantityRequired.Unit != "" {
					qtyStr += " " + p.QuantityRequired.Unit
				}
			}

			// Format product line
			productLine := prodName
			if specStr != "" {
				productLine += " (" + specStr + ")"
			}
			if qtyStr != "" {
				productLine += " - " + qtyStr
			}
			sb.WriteString(productLine + ".\n")

			// Price Line
			if p.Price.Value != 0 {
				var priceParts []string
				priceParts = append(priceParts, fmt.Sprintf("%v", p.Price.Value))

				if p.Price.Currency != "" && strings.ToLower(p.Price.Currency) != "others" && strings.ToLower(p.Price.Currency) != "other" {
					priceParts = append(priceParts, p.Price.Currency)
				}

				if p.Price.PriceUnit != "" {
					unit := strings.TrimSpace(p.Price.PriceUnit)
					if strings.HasPrefix(strings.ToLower(unit), "per ") {
						priceParts = append(priceParts, unit)
					} else {
						priceParts = append(priceParts, "per "+unit)
					}
				}

				priceLine := "Price quoted: " + strings.Join(priceParts, " ")

				if !p.Price.GST.IsGSTIncluded {
					if p.Price.GST.Value > 0 {
						priceLine += fmt.Sprintf(" + %v %% GST", p.Price.GST.Value)
					} else if p.Price.GST.Details != "" {
						priceLine += " + GST"
					}
				}

				if !p.Price.DeliveryCharge.IsIncluded {
					if p.Price.DeliveryCharge.Value > 0 {
						priceLine += fmt.Sprintf(" + delivery charges(%v)", p.Price.DeliveryCharge.Value)
					} else if p.Price.DeliveryCharge.Details != "" {
						priceLine += " + delivery charges"
					}
				}

				sb.WriteString(priceLine + "\n")
			}
			sb.WriteString("\n")
		}

		// Quantity required line (only if uniform, > 0, AND multiple products)
		if allQtiesSame && firstVal > 0 && targetProdsCount > 1 {
			qtyLine := fmt.Sprintf("(Quantity required: %v", firstVal)
			if firstUnit != "" {
				qtyLine += " " + firstUnit
			}
			qtyLine += ")"
			sb.WriteString(qtyLine + "\n\n")
		}

		// 4. Next steps
		if len(cen_data.NextSteps.SellerNextSteps) > 0 {
			sb.WriteString("Next steps:\n")
			for _, step := range cen_data.NextSteps.SellerNextSteps {
				btnText := CapitalizeFirst(step)
				if !strings.HasPrefix(strings.ToLower(btnText), "to ") {
					btnText = "To " + btnText
				}
				sb.WriteString(fmt.Sprintf("-%s\n", btnText))
			}
		}

		return sb.String(), cleanJSON, promptTokens, completionTokens, nil
	}
	return "", "", 0, 0, fmt.Errorf(str_err)
}

func llm_req(modelToRun string, b64_audiobytes string, timeout_seconds int, isRetry int, categories []string, userDetails map[string]string) (*http.Response, error, float64) {
	if isRetry == 1 {
		time.Sleep(30 * time.Second)
	}

	// Dynamic adjustments based on category availability
	currentSystemRole := systemRole
	if len(categories) > 0 {
		// Replace first line as requested by user
		firstLineEnd := strings.Index(systemRole, "\n")
		restOfRole := ""
		if firstLineEnd != -1 {
			restOfRole = systemRole[firstLineEnd:]
		}
		currentSystemRole = "You are an expert product extractor with deep knowledge of the Indian B2B market, product specifications, manufacturing standards, and category knowledge." + restOfRole
	}

	prompt := fmt.Sprintf(prompt,
		userDetails["buyer_name"],
		userDetails["buyer_city"],
		userDetails["buyer_number"],
		userDetails["seller_name"],
		userDetails["seller_city"],
	)

	// Prepare properties for product items
	productProperties := map[string]interface{}{
        "product_name": map[string]interface{}{
            "type":        "string",
            "maxLength":   100,
            "description": "Product name standardized to the seller's wording. If the buyer uses a different wording for the same product, append the buyer's term in brackets. Do not include this field if unclear.",
        },
        "in_stock": map[string]interface{}{
            "type":        "string",
            "description": "Whether the product is in stock or out of stock, as clearly implied from the conversation. (Set 'out of stock' only if the seller explicitly says it's unavailable, 'not sold' only if the seller explicitly says they don't sell/deal in it and 'in stock' if availability is clearly implied from the call). Do not include this field if not stated or unclear.",
            "enum":        []string{"in stock", "out of stock", "not sold"},
        },
        "is_buyer_interested": map[string]interface{}{
            "type":        "boolean",
            "description": "True if the call shows clear buyer interest/purchase intent for this product, otherwise false. Can be true for multiple products.",
        },
        "price": map[string]interface{}{
            "type":                 "object",
            "description":          "Structured price for this product",
            "additionalProperties": false,
            "properties": map[string]interface{}{
                "value": map[string]interface{}{
                    "type":        "number",
                    "description": "Numeric value of price required for this product, exactly as explicitly stated by the seller in the call (e.g., 25000, 150, 8.5, etc.). If a range is given, record the lower bound. If an approximate value is given, record the stated number. Do not include this field if non numeric, zero, negative, not stated or unclear.",
                },
                "currency": map[string]interface{}{
                    "type":        "string",
                    "description": "Currency for this product's price exactly as explicitly stated or confirmed by the seller in the call, normalized to INR/USD/EUR/GBP/Others. Do not include this field if not stated or unclear.",
                    "enum":        []string{"INR", "USD", "EUR", "GBP", "Others"},
                },
                "price_unit": map[string]interface{}{
                    "type":        "string",
                    "maxLength":   30,
                    "description": "Pricing unit for this product's price exactly as explicitly stated or confirmed by the seller in the call (e.g., piece, kg, meter, 10 kg, 50 pieces, etc.). Do not include this field if not stated or unclear.",
                },
                "price_reaction_by_buyer": map[string]interface{}{
                    "type":        "string",
                    "maxLength":   150,
                    "description": "Omit if price is not discussed. Capture the reaction using one of these categories: 'Too High', 'Negotiating', or 'Accepted'. Format: '[Category]: (Verbatim Quote)'. Use English characters only for transliteration. Examples: 'Too High: (Rate bahut zyada hai)', 'Negotiating: (Thoda kam karo)', 'Accepted: (Theek hai rate done hai)', 'Negotiating: (Local market is cheaper)'.",
                },
                "price_negotiation_notes": map[string]interface{}{
                    "type":        "string",
                    "maxLength":   200,
                    "description": "Details of any discount requests, freight charges, or price gaps discussed.",
                },
                "gst": map[string]interface{}{
                    "type":                 "object",
                    "additionalProperties": false,
                    "properties": map[string]interface{}{
                        "is_gst_included": map[string]interface{}{
                            "type":        "boolean",
                            "description": "Whether GST is included in the quoted price for this product or not as clearly implied from the conversation. Do not include this field if not stated or unclear.",
                        },
                        "value": map[string]interface{}{
                            "type":        "number",
                            "description": "GST percentage for this product exactly as explicitly stated or confirmed by the seller in the call (e.g., 5, 18, etc.). Do not include this field if non numeric, zero, negative, decimal, not stated or unclear.",
                        },
                        "details": map[string]interface{}{
                            "type":        "string",
                            "maxLength":   150,
                            "description": "Any GST related details for this product exactly as explicitly stated or confirmed by the seller in the call (e.g, 5% GST if cash payment otherwise 18%, etc.). Do not include this field if not stated or unclear.",
                        },
                    },
                },
                "delivery_charge": map[string]interface{}{
                    "type":                 "object",
                    "additionalProperties": false,
                    "properties": map[string]interface{}{
                        "is_delivery_charge_included": map[string]interface{}{
                            "type":        "boolean",
                            "description": "Whether delivery charge is included in the quoted price for this product or not as clearly implied from the conversation. Do not include this field if not stated or unclear.",
                        },
                        "value": map[string]interface{}{
                            "type":        "number",
                            "description": "Numeric value of delivery charge required for this product, exactly as explicitly stated by the seller in the call (e.g., 25000, 150, 8.5, etc.). If a range is given, record the lower bound. If an approximate value is given, record the stated number. Do not include this field if non numeric, zero, negative, not stated or unclear.",
                        },
                        "currency": map[string]interface{}{
                            "type":        "string",
                            "description": "Currency for this product's delivery charge exactly as explicitly stated or confirmed by the seller in the call, normalized to INR/USD/EUR/GBP/Others. Do not include this field if not stated or unclear.",
                            "enum":        []string{"INR", "USD", "EUR", "GBP", "Others"},
                        },
                        "details": map[string]interface{}{
                            "type":        "string",
                            "maxLength":   150,
                            "description": "Any delivery related details exactly as explicitly stated or confirmed by the seller in the call (e.g, Delivery to Delhi within 1 day, etc.). Do not include this field if not stated or unclear.",
                        },
                    },
                },
                "offer_details": map[string]interface{}{
                    "type":        "string",
                    "maxLength":   100,
                    "description": "Any offer/discount related details exactly as explicitly stated or confirmed by the seller in the call (e.g, 10% discount on bulk purchase, etc.). Do not include this field if not stated or unclear.",
                },
            },
        },
        "quantity_required": map[string]interface{}{
            "type":                 "object",
            "description":          "Structured quantity for this product",
            "additionalProperties": false,
            "properties": map[string]interface{}{
                "value": map[string]interface{}{
                    "type":        "number",
                    "description": "Numeric value of quantity required for this product exactly as explicitly stated or confirmed by the buyer in the call (e.g., 5, 15, 3.5, etc.). If a range is given, record the lower bound. If an approximate value is given, record the stated number. Do not include this field if non numeric, zero, negative, not stated or unclear.",
                },
                "unit": map[string]interface{}{
                    "type":        "string",
                    "maxLength":   30,
                    "description": "Unit for this product's quantity exactly as explicitly stated or confirmed by the buyer in the call (e.g., pieces, kg, liters, etc.). Do not include this field if not stated or unclear.",
                },
            },
        },
        "quantity_accepted_by_seller": map[string]interface{}{
            "type":        "boolean",
            "description": "Omit if quantity is not discussed. Set to 'false' ONLY if the seller explicitly rejects the volume (e.g., 'we don't give 2kg' or 'cannot do 10,000 units'). Set to 'true' if the buyer mentions a quantity and the seller continues the discussion (pricing, shipping, etc.) without rejecting the volume, as acceptance is implied.",
        },
        "specifications": map[string]interface{}{
            "type":        "array",
            "description": "Specifications for this product. Variations must be separate products",
            "items": map[string]interface{}{
                "type":                 "object",
                "additionalProperties": false,
                "properties": map[string]interface{}{
                    "name": map[string]interface{}{
                        "type":        "string",
                        "maxLength":   50,
                        "description": "Specification name for this product exactly as explicitly stated by any speaker in the call (e.g., Input Voltage, Phase, Capacity, etc.). Do not include this field if not stated or unclear.",
                    },
                    "value": map[string]interface{}{
                        "type":        "string",
                        "maxLength":   100,
                        "description": "Numeric or non numeric value for this specification exactly as explicitly stated by any speaker in the call (e.g., 5, 9, -1, 0.9, high, red, M, large, etc.). If a numeric range is given, record the lower bound. If an approximate numeric value is given, record the stated number. If non numeric, record the same exactly as stated. Do not include this field if not stated or unclear.",
                    },
                    "unit": map[string]interface{}{
                        "type":        "string",
                        "maxLength":   20,
                        "description": "The unit for this specification exactly as explicitly stated by any speaker in the call (e.g., V, A, kg, mm, pcs, piece). Do not include this field if not stated or unclear.",
                    },
                    "buyer_requested": map[string]interface{}{
                        "type":        "boolean",
                        "description": "True if the buyer explicitly asked about this specification (question/request), otherwise false.",
                    },
                    "seller_mentioned": map[string]interface{}{
                        "type":        "boolean",
                        "description": "True if the seller explicitly stated/provided this specification in the call, otherwise false.",
                    },
                },
            },
        },
    }


    // Add category fields if mcatID is not 0
    if len(categories) > 0 {
        productProperties["most_specific_category"] = map[string]interface{}{
            "type":                 "object",
            "additionalProperties": false,
            "properties": map[string]interface{}{
                "name": map[string]interface{}{
                    "type":        "string",
                    "description": "The most specific category that best describes this product based on product name and specifications. Do not include this field if unclear.",
                    "enum":        categories,
                },
                "reason": map[string]interface{}{
                    "type":        "string",
                    "maxLength":   200,
                    "description": "Detailed rationale explaining why this is the most specific category for this product. Do not include this field if unclear.",
                },
            },
        }
        productProperties["related_categories"] = map[string]interface{}{
            "type":        "array",
            "description": "All the related categories which the product can be mapped into",
            "items": map[string]interface{}{
                "type":                 "object",
                "additionalProperties": false,
                "properties": map[string]interface{}{
                    "name": map[string]interface{}{
                        "type":        "string",
                        "description": "Related categories this product can fall into based on product name and specifications. Do not include this field if unclear.",
                        "enum":        categories,
                    },
                    "reason": map[string]interface{}{
                        "type":        "string",
                        "maxLength":   200,
                        "description": "Short rationale explaining why this product also belongs to these related categories. Do not include this field if unclear.",
                    },
                },
            },
        }
    }



	body := map[string]interface{}{
		"model": modelToRun,
		
		"response_format": map[string]interface{}{
			"type": "json_schema",
			"json_schema": map[string]interface{}{
				"name":   "call_extraction_output",
				"strict": true,
				"schema": map[string]interface{}{
					"type":                 "object",
					"description":          "Root schema for call extraction result",
					"additionalProperties": false,
					"required":             []string{"buyer_details", "seller_details", "products", "minimum_order_quantity", "lead_tag", "payment", "call_back", "next_steps", "metadata"},
					"properties": map[string]interface{}{
						"buyer_details": map[string]interface{}{
							"type":                 "object",
							"additionalProperties": false,
							"properties": map[string]interface{}{
								"buyer_name": map[string]interface{}{
									"type":        "string",
									"maxLength":   60,
									"description": "Buyer's name exactly as explicitly stated or confirmed by the buyer in the call (e.g., Rahul, RM Gupta, Udit Singh, Mohit Rao Singh, etc.). Do not include company names. Do not include this field if not stated or unclear.",
								},
								"buyer_mobile_number": map[string]interface{}{
									"type":        "number",
									"description": "Buyer's phone number exactly as explicitly stated or confirmed by the buyer in the call (e.g., 9384358674, etc.). Do not include this field if not stated or unclear.",
								},
								"buyer_location": map[string]interface{}{
									"type":                 "object",
									"description":          "Buyer's location details.",
									"additionalProperties": false,
									"properties": map[string]interface{}{
										"buyer_city": map[string]interface{}{
											"type":        "string",
											"maxLength":   60,
											"description": "Buyer's city exactly as explicitly stated or confirmed by the buyer in the call. (e.g., New Delhi, Mumbai, New York, Chicago, etc.). Do not include this field if not stated or unclear.",
										},
										"buyer_state": map[string]interface{}{
											"type":        "string",
											"maxLength":   60,
											"description": "Buyer's state exactly as explicitly stated or confirmed by the buyer in the call. (e.g., Uttar Pradesh, Telangana, Gujarat, Maharashtra, Texas, etc.). Do not include this field if not stated or unclear.",
										},
										"buyer_locality": map[string]interface{}{
											"type":        "string",
											"maxLength":   100,
											"description": "Buyer's locality within the city/region exactly as explicitly stated or confirmed by the buyer in the call (e.g., village/town, area/sector/street/colony, neighborhood/landmark, PIN/postcode, etc. if mentioned; e.g., 'Sec 61', 'Bank Street', 'near Gomti Park', 'Gachibowli', 'Rampur village', '500081', etc.). Do not include this field if not stated or unclear.",
										},
									},
								},
							},
						},
						"seller_details": map[string]interface{}{
							"type":                 "object",
							"additionalProperties": false,
							"properties": map[string]interface{}{
								"seller_name": map[string]interface{}{
									"type":        "string",
									"maxLength":   60,
									"description": "Seller's name exactly as explicitly stated or confirmed by the seller in the call. Do not include company names. (e.g., Rahul, RM Gupta, Udit Singh, Mohit Rao Singh, etc.). Do not include this field if not stated or unclear.",
								},
								"seller_mobile_number": map[string]interface{}{
									"type":        "number",
									"description": "Seller's phone number exactly as explicitly stated or confirmed by the seller in the call (e.g., 9384358674, etc.). Do not include this field if not stated or unclear.",
								},
								"seller_location": map[string]interface{}{
									"type":                 "object",
									"description":          "Seller's location details.",
									"additionalProperties": false,
									"properties": map[string]interface{}{
										"seller_city": map[string]interface{}{
											"type":        "string",
											"maxLength":   60,
											"description": "Seller's city exactly as explicitly stated or confirmed by the seller in the call. (e.g., New Delhi, Mumbai, New York, Chicago, etc.). Do not include this field if not stated or unclear.",
										},
										"seller_state": map[string]interface{}{
											"type":        "string",
											"maxLength":   60,
											"description": "Seller's state exactly as explicitly stated or confirmed by the seller in the call. (e.g., Uttar Pradesh, Telangana, Gujarat, Maharashtra, Texas, etc.). Do not include this field if not stated or unclear.",
										},
										"seller_locality": map[string]interface{}{
											"type":        "string",
											"maxLength":   100,
											"description": "Seller's locality within the city/region exactly as explicitly stated or confirmed by the seller in the call (e.g., village/town, area/sector/street/colony, neighborhood/landmark, PIN/postcode, etc. if mentioned, e.g., 'Sec 61', 'Bank Street', 'near Gomti Park', 'Gachibowli', 'Rampur village', '500081', etc.). Do not include this field if not stated or unclear.",
										},
									},
								},
							},
						},
						"products": map[string]interface{}{
							"type":        "array",
							"description": " The list of products discussed in the call (one or more). Include a separate entry for each distinct variation - if any specification differs (e.g., size/color/model, etc.), treat it as a new product.",
							"items": map[string]interface{}{
								"type":                 "object",
								"additionalProperties": false,
								"properties":           productProperties, // maxLength additions should be made inside productProperties separately
							},
						},
						"minimum_order_quantity": map[string]interface{}{
							"type":                 "object",
							"additionalProperties": false,
							"properties": map[string]interface{}{
								"value": map[string]interface{}{
									"type":        "number",
									"description": "Numeric value of minimum order quantity (MOQ) exactly as explicitly stated or confirmed by the seller in the call  (e.g., 10, 100, 3.5, etc.). If a range is given, record the lower bound. If an approximate value is given, record the stated number. Do not include this field if non numeric, zero, negative, not stated or unclear.",
								},
								"unit": map[string]interface{}{
									"type":        "string",
									"maxLength":   30,
									"description": "The unit of the MOQ exactly as explicitly stated or confirmed by the seller in the call (e.g., pieces, kg, liters, etc.). Do not include this field if not stated or unclear.",
								},
							},
						},
						"lead_tag": map[string]interface{}{
							"type":        "object",
							"description": "Analytical insights regarding deal probability and urgency. This helps sellers quickly identify high-value opportunities.",
							"maxLength":   200,
							"additionalProperties": false,
							"required":             []string{"deal_readiness", "deal_readiness_reason", "deal_blockers"},
							"properties": map[string]interface{}{
								"deal_readiness": map[string]interface{}{
									"type":        "string",
									"enum":        []string{"Cold", "Warm", "Hot"},
									"description": "The 'Temperature' of the lead. 'Hot': Ready to pay/visit now or needs immediate delivery. 'Warm': Serious intent but delayed by factors like samples or boss approval. 'Cold': Just checking rates, no timeline, or serious MOQ/location mismatch.",
								},
								"deal_readiness_reason": map[string]interface{}{
									"type":        "string",
									"maxLength":   200,
									"description": "A crisp, 1-sentence explanation for the tag. Focus on 'Why' and 'When'. Never use the words 'buyer' or 'seller'. Example: 'Urgent requirement for tomorrow delivery' or 'Just researching prices for next month'.",
								},
								"deal_blockers": map[string]interface{}{
									"type":        "array",
									"description": "List specific friction points. Use 'Category: Detail' format. Omit if none. Examples: 'Location: No delivery to Assam', 'MOQ: Requirement below minimum limit', 'Payment: Requesting 30-day credit'.",
									"items": map[string]interface{}{
										"type":      "string",
										"maxLength": 100,
									},
								},
							},
						},
						"payment": map[string]interface{}{
							"type":                 "object",
							"additionalProperties": false,
							"properties": map[string]interface{}{
								"payment_mode": map[string]interface{}{
									"type":        "string",
									"description": "Primary payment mode as clearly implied from the conversation. Do not include this field if not stated or unclear.",
									"enum":        []string{"upi", "cash", "card", "net banking", "bank transfer", "cheque", "emi", "credit", "cod", "wallet"},
								},
								"payment_details": map[string]interface{}{
									"type":        "string",
									"maxLength":   200,
									"description": "Any payment related details exactly as explicitly stated or confirmed by the seller in the call (e.g., advance payment, loan/emi related details, terms & conditions, etc.). Do not include this field if not stated or unclear.",
								},
							},
						},
						"call_back": map[string]interface{}{
							"type":        "string",
							"maxLength":   200,
							"description": "Omit if the seller does not explicitly promise a return call. Capture the seller's commitment using English characters only. Format: 'callback by [Time/Date IST] - [Context]'. Translate non-English speech and include transliteration in brackets. Examples: 'callback by Tomorrow 5 PM - To finalize (Kal 5 baje call karta hoon)', 'callback by In 1 hour - To confirm stock (Ek ghante mein call karta hoon)', 'callback by Monday 1 PM - Detailed discussion'.",
						},
						"next_steps": map[string]interface{}{
							"type":                 "object",
							"additionalProperties": false,
							"properties": map[string]interface{}{
								"buyer_next_steps": map[string]interface{}{
									"type":        "array",
									"description": "Any actions to be taken by the buyer after the call as clearly implied from the conversation (e.g., 'share delivery address', 'confirm price/quantity/size/color', 'make advance payment', 'visit the shop/warehouse', 'send reference photos/specs', 'call back at a specific time', etc.). Ensure every actionable starts with 'To' and never uses the words 'buyer' or 'seller'.",
									"items": map[string]interface{}{
										"type":      "string",
										"maxLength": 150,
									},
								},
								"seller_next_steps": map[string]interface{}{
									"type":        "array",
									"description": "Precise, high-intent actions for the seller. Constraints: 1. Must start with 'To'. 2. Do not use 'buyer' or 'seller'. 3. Omit passive actions like 'To wait for message' or 'To stay in touch'. 4. Return an EMPTY array if the deal is dead (e.g., buyer explicitly refused, bought elsewhere, or is irrelevant). Examples: 'To share catalog on WhatsApp', 'To confirm 500pc stock availability', 'To send bank details for advance payment'.",
									"items": map[string]interface{}{
										"type":      "string",
										"maxLength": 150,
									},
								},
							},
						},
						"metadata": map[string]interface{}{
							"type":                 "object",
							"description":          "Call-level metadata",
							"required":             []string{"buyer_intent", "intended_application", "buyer_conclusion", "primary_language", "all_languages", "call_type", "additional_details", "call_purpose"},
							"additionalProperties": false,
							"properties": map[string]interface{}{
								"buyer_intent": map[string]interface{}{
									"type":                 "object",
									"required":             []string{"narrative", "intent_level", "reasoning"},
									"additionalProperties": false,
									"properties": map[string]interface{}{
										"intent_level": map[string]interface{}{
											"type":        "string",
											"enum":        []string{"Low", "Medium", "High"},
											"description": "Classification of requirement depth. 'High': Clear specs, project context, or urgency. 'Medium': Genuine interest but still in research phase. 'Low': Casual price-checking or vague inquiry.",
										},
										"narrative": map[string]interface{}{
											"type":        "string",
											"maxLength":   200,
											"description": "A crisp summary of the buyer's requirement in English (e.g., 'Wants 500 units for a construction project in Noida').",
										},
										"reasoning": map[string]interface{}{
											"type":        "string",
											"maxLength":   300,
											"description": "The specific evidence for the intent level. Explain why it was classified as such based on the buyer's technical knowledge, urgency, or specific questions asked. Use English characters only.",
										},
									},
								},
								"intended_application": map[string]interface{}{
									"type":        "string",
									"maxLength":   50,
									"description": "A crisp 2-4 word noun phrase identifying the buyer's end-use. Avoid full sentences. Examples: 'Retail Resale', 'Home Renovation', 'Industrial Production', 'Government Tender', 'Sample Testing'. Omit if not explicitly mentioned.",
								},
								"buyer_conclusion": map[string]interface{}{
									"type":                 "object",
									"description":          "The final transactional outcome of the call. Never use the words 'buyer' or 'seller'.",
									"required":             []string{"category", "conclusion_notes"},
									"additionalProperties": false,
									"properties": map[string]interface{}{
										"category": map[string]interface{}{
											"type":        "string",
											"enum":        []string{"Committed", "Pending", "Follow-up", "Lost"},
											"description": "The final status of the deal. 'Committed': Deal finalized or payment discussed. 'Pending': Interested but comparing or checking details. 'Follow-up': Needs internal discussion or later callback. 'Lost': Explicit refusal or bought elsewhere.",
										},
										"conclusion_notes": map[string]interface{}{
											"type":        "string",
											"maxLength":   300,
											"description": "Specific reason for the status including verbatim transliteration in brackets. Use English characters only. Do not use 'buyer' or 'seller'. Examples: 'Deal finalized, send details (Details bhejo deal final hai)', 'Will check local rates (Local mein rate check karke batata hoon)', 'Price too high, looking locally (Price zyada hai local mein dekhunga)'.",
										},
									},
								},
								"primary_language": map[string]interface{}{
									"type":        "string",
									"maxLength":   30,
									"description": "The language most predominantly used throughout the call",
								},
								"all_languages": map[string]interface{}{
									"type":        "array",
									"description": "Languages detected across the duration of the call",
									"items": map[string]interface{}{
										"type":      "string",
										"maxLength": 30,
									},
								},
								"call_type": map[string]interface{}{
									"type":                 "object",
									"description":          "Structured call type classification with reasoning for the call",
									"additionalProperties": false,
									"properties": map[string]interface{}{
										"evidence": map[string]interface{}{
											"type":                 "object",
											"description":          "Supporting evidence and context indicators for the call type classification",
											"additionalProperties": false,
											"properties": map[string]interface{}{
												"buyer_persona": map[string]interface{}{
													"type":        "string",
													"maxLength":   60,
													"description": "Identified buyer persona based on conversation context (e.g., shop keeper, end customer). Output an empty string \"\" if unclear.",
												},
												"quantity_scale": map[string]interface{}{
													"type":        "string",
													"description": "Scale of quantity discussed - 'high' for bulk/industrial quantities, 'low' for retail/small quantities. Do not include this field if unclear.",
													"enum":        []string{"High", "Low"},
												},
												"order_type": map[string]interface{}{
													"type":        "string",
													"maxLength":   50,
													"description": "Type of order discussed (e.g., sample, bulk, one-time, recurring, trial, commercial). Do not include this field if unclear.",
												},
												"repeat_buyer": map[string]interface{}{
													"type":        "boolean",
													"description": "True If buyer implied/mentioned recurring purchase otherwise False for one-time purchase. Do not include this field if unclear.",
												},
												"keywords": map[string]interface{}{
													"type":        "array",
													"description": "Key business or personal context indicators mentioned (e.g., 'Export Quality', 'Commercial Project', 'Home Renovation', 'Factory Use', 'Retail Shop'). Do not include this field if unclear.",
													"items": map[string]interface{}{
														"type":      "string",
														"maxLength": 50,
													},
												},
											},
										},
										"reason": map[string]interface{}{
											"description": "Detailed reasoning behind the call type classification based on call context. Do not include this field if unclear.",
											"type":        "string",
											"maxLength":   400,
										},
										"type": map[string]interface{}{
											"description": "Whether the call is B2B or B2C. Do not include this field if unclear.",
											"enum":        []string{"B2B", "B2C"},
											"type":        "string",
										},
									},
								},
								"additional_details": map[string]interface{}{
									"type":                 "object",
									"required":             []string{"buyer_queries", "seller_queries"},
									"additionalProperties": false,
									"properties": map[string]interface{}{
										"buyer_queries": map[string]interface{}{
											"type": "array",
											"items": map[string]interface{}{
												"type":                 "object",
												"required":             []string{"query", "sequence", "category"},
												"additionalProperties": false,
												"properties": map[string]interface{}{
													"query": map[string]interface{}{
														"type":        "string",
														"maxLength":   200,
														"description": "A list of specific, unanswered questions or technical inquiries made by the buyer. Constraints: 1. Focus on questions regarding pricing, credit, delivery, technical specs, or certifications. 2. Translate any non-English questions into English. 3. Omit general pleasantries or small talk (e.g., 'How are you?'). 4. If no specific questions were asked, return an empty array []. Examples: 'Is a 15-day credit period possible?', 'Can you deliver to Noida by Friday?', 'Does the product come with an ISO certificate?'",
													},
													"sequence": map[string]interface{}{
														"type":        "integer",
														"description": "The sequential order index of this exact query among ALL queries asked. The very first query asked by anyone gets 1, the next gets 2, etc. Do not skip numbers. Do not count statements or answers.",
													},
													"category": map[string]interface{}{
														"type":      "string",
														"maxLength": 50,
													},
												},
											},
										},
										"seller_queries": map[string]interface{}{
											"type": "array",
											"items": map[string]interface{}{
												"type":                 "object",
												"required":             []string{"query", "sequence", "category"},
												"additionalProperties": false,
												"properties": map[string]interface{}{
													"query": map[string]interface{}{
														"type":        "string",
														"maxLength":   200,
														"description": "List of specific dynamic inquiries (Price, Specs, Credit). Omit pleasantries. Empty array if none.",
													},
													"sequence": map[string]interface{}{
														"type":        "integer",
														"description": "The sequential order index of this exact query among ALL queries asked. The very first query asked by anyone gets 1, the next gets 2, etc. Do not skip numbers. Do not count statements or answers.",
													},
													"category": map[string]interface{}{
														"type":      "string",
														"maxLength": 50,
													},
												},
											},
										},
									},
								},
								"call_purpose": map[string]interface{}{
									"description": "Primary purpose of the call as clearly implied from the conversation (requirement=buyer inquiry/interest/purchase need, complaint=issue with order/product/payment/delivery/return/service, support=how-to/technical/help, followup=callback/status update, irrelevant=spam/wrong number/non business/call not connected).",
									"enum":        []string{"requirement", "complaint", "support", "followup", "irrelevant"},
									"type":        "string",
								},
							},
						},
					},
				},
			},
		},
		"messages": []map[string]interface{}{
			{
				"role":    "system",
				"content": currentSystemRole,
			},
			{
				"role": "user",
				"content": []map[string]interface{}{
					{
						"type": "input_audio",
						"input_audio": map[string]string{
							"data":   b64_audiobytes,
							"format": "mp3",
						},
					},
					{
						"type": "text",
						"text": prompt,
					},
				},
			},
		},
	}

	jsonBody, _ := json.Marshal(body)
	req, _ := http.NewRequest(
		"POST",
		"https://imllm.intermesh.net/v1/chat/completions",
		bytes.NewBuffer(jsonBody),
	)
	req.Header.Set("Authorization", "Bearer "+IM_LLM_KEY)
	req.Header.Set("Content-Type", "application/json")

	//fmt.Println("Sending request to LLM API...")

	client := &http.Client{
		Timeout: time.Duration(timeout_seconds) * time.Second,
	}

	t0 := time.Now()
	resp, err := client.Do(req)
	apiTime := time.Since(t0).Seconds()
	return resp, err, apiTime
}

func extractAssistantContent(p map[string]interface{}) (string, string, error) {
	c, ok := p["choices"].([]interface{})
	if !ok || len(c) == 0 {
		return "", "", fmt.Errorf("no choices")
	}

	choice, ok := c[0].(map[string]interface{})
	msg, ok := choice["message"].(map[string]interface{})
	if !ok {
		return "", "", fmt.Errorf("bad message structure")
	}

	content, ok := msg["content"].(string)
	if !ok {
		return "", "", fmt.Errorf("missing content")
	}

	// Try to extract thought/reasoning if available
	var thought string
	if t, ok := choice["thought"].(string); ok {
		thought = t
	} else if t, ok := msg["thought"].(string); ok {
		thought = t
	}

	return content, thought, nil
}


func CapitalizeFirst(s string) string {
	if len(s) == 0 {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

func CentralizedPayload(loggedin_glid string, contact_glid string, modid string, ak string) ([]byte, error) {
	// Fetch the API URL from the environment variable

	apiURL := "http://lms.imutils.com/addressbook/giveSuggestivePayload"
	if apiURL == "" {
		return nil, fmt.Errorf("environment variable CENTRALIZED_PAYLOAD_API_URL not set")
	}
	// server_ak := os.Getenv("SERVER_AK")
	// if server_ak == "" {
	// 	return nil, fmt.Errorf("environment variable SERVER_AK not set")
	// }

	// Prepare the payload
	payload := map[string]interface{}{
		"glusrid":       loggedin_glid,
		"contacts_glid": contact_glid,
		"modid":         modid,
		"AK":            ak,
	}

	// Call the API using the CallAPI function
	response, err := CallAPI(apiURL, payload)
	if err != nil {
		return nil, err
	}
	return response, nil
}

func CallAPI(apiURL string, payload map[string]interface{}) ([]byte, error) {
	// Convert the payload to JSON
	jsonData, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal payload: %v", err)
	}
	// fmt.Print("\nAPI uRL : ",apiURL,"\n")
	// Create an HTTP POST request
	req, err := http.NewRequest("POST", apiURL, bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, fmt.Errorf("failed to create HTTP request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	// Start total request timer
	// totalStart := time.Now()

	// Send the request
	client := optimizedHttpClient
	resp, err := client.Do(req)
	// fmt.Print("From line169",resp, err)
	if err != nil {
		return nil, fmt.Errorf("failed to send HTTP request: %v", err)
	}
	defer resp.Body.Close()

	// Calculate total request execution time
	// totalDuration := time.Since(totalStart)
	// fmt.Printf("Total request execution time: %v\n", totalDuration)

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %v", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("received non-200 status code: %d, body: %s", resp.StatusCode, string(body))
	}

	return body, nil
}

func processJSONData(jsonData string, buyerID string, sellerID string) (map[string]interface{}, map[string]interface{}, map[string]interface{}) {
	var apiResponse map[string]interface{}
	err := json.Unmarshal([]byte(jsonData), &apiResponse)
	if err != nil {
		fmt.Println(err)
		return nil, nil, nil
	}

	result, _ := apiResponse["result"].(map[string]interface{})

	var buyerDetails, sellerDetails map[string]interface{}
	var transactionDetails map[string]interface{}

	// Extract buyer details
	buyerData, buyerOk := result[buyerID].(map[string]interface{})
	if buyerOk {
		buyerDetails = extractUserDetails(buyerData)
	}

	// Extract seller details
	sellerData, sellerOk := result[sellerID].(map[string]interface{})
	if sellerOk {
		sellerDetails = extractUserDetails(sellerData)
	}

	// Extract transaction details
	txnDetails, txnOk := result["transaction_details"].(map[string]interface{})
	if txnOk {
		transactionDetails = txnDetails
	}

	return buyerDetails, sellerDetails, transactionDetails
}

// extractUserDetails extracts relevant user information from the provided data
func extractUserDetails(userData map[string]interface{}) map[string]interface{} {
	userDetails := make(map[string]interface{})

	// Extract GL user details
	glusrDetails, ok := userData["glusr_details"].(map[string]interface{})
	if ok {
		userDetails["Fname"] = getString(glusrDetails["fname"])
		userDetails["State"] = getString(glusrDetails["state"])
		userDetails["Country"] = getString(glusrDetails["country"])
		userDetails["City"] = getString(glusrDetails["city"])
		userDetails["CatalogLink"] = getString(glusrDetails["catalog_link"])
		userDetails["CompanyName"] = getString(glusrDetails["glusr_usr_companyname"])
		userDetails["PaidFreeSeller"] = getString(glusrDetails["paid_free_seller"])
		userDetails["Email"] = getString(glusrDetails["email"])
		userDetails["ContactNumber"] = getString(glusrDetails["contact_number"])
	}

	// Extract and process messages
	messages, ok := userData["messages"].([]interface{})
	if ok && len(messages) > 0 {
		var messageList []map[string]string
		for _, message := range messages {
			msg, ok := message.(map[string]interface{})
			if !ok {
				continue
			}

			// Initialize message text to empty string
			messageText := ""
			if msgText, ok := msg["msg_text"].(map[string]interface{}); ok {
				if rawText, exists := msgText["message_text"]; exists && rawText != nil {
					if strText, ok := rawText.(string); ok {
						messageText = strText
					}
				}
			}

			// Create message details map
			messageDetails := map[string]string{
				"MessageText":     messageText,
				"SenderID":        getString(msg["sender_id"]),
				"MsgType":         getString(msg["msg_type"]),
				"MsgSentTime":     getString(msg["msg_sent_time"]),
				"MsgSenderbsFlag": getString(msg["bs_flag"]),
			}
			messageList = append(messageList, messageDetails)
		}
		userDetails["Messages"] = messageList
	} else {
		userDetails["Messages"] = []map[string]string{}
	}

	return userDetails
}

func getString(value interface{}) string {
	if str, ok := value.(string); ok {
		return str
	}
	return ""
}


