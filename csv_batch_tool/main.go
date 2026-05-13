package main

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func main() {
	// if len(os.Args) < 3 {
	// 	fmt.Println("Usage: go run . <input.csv> <output.csv>")
	// 	return
	// }

	inputPath := filepath.Join("input", "filtered_output_20-04.csv")
	if envInput := os.Getenv("PNS_INPUT_CSV"); envInput != "" {
		inputPath = envInput
	}

	modelName := "google/gemini-2.5-pro"
	if envModel := os.Getenv("PNS_EXTRACTION_MODEL"); envModel != "" {
		modelName = envModel
	}

	timestamp := time.Now().Format("20060102_150405")
	outputDir := filepath.Join("extraction_output_30-04", sanitizeForPath(modelName), timestamp)
	outputPath := filepath.Join(outputDir, "output.csv")

	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		fmt.Printf("Error creating output directory %s: %v\n", outputDir, err)
		return
	}

	// Load prompts_override.json if it exists (written by run_pipeline.py from prompts_config.json)
	if data, err := os.ReadFile("prompts_override.json"); err == nil {
		var override struct {
			Prompt     string `json:"prompt"`
			SystemRole string `json:"system_role"`
		}
		if json.Unmarshal(data, &override) == nil {
			if override.Prompt != "" {
				prompt = override.Prompt
			}
			if override.SystemRole != "" {
				systemRole = override.SystemRole
			}
			fmt.Println("Loaded prompts from prompts_override.json")
		}
	}

	if err := InitializeCategoryData(); err != nil {
		fmt.Printf("Warning: Failed to load category data: %v\n", err)
	}

	f, err := os.Open(inputPath)
	if err != nil {
		fmt.Printf("Error opening input file %s: %v\n", inputPath, err)
		return
	}
	defer f.Close()

	reader := csv.NewReader(f)
	records, err := reader.ReadAll()
	if err != nil {
		fmt.Printf("Error reading CSV: %v\n", err)
		return
	}

	if len(records) < 2 {
		fmt.Println("CSV empty or missing header")
		return
	}

	header := records[0]
	sellerIdx, buyerIdx, urlIdx, fileIDIdx := -1, -1, -1, -1
	for i, h := range header {
		h = strings.ToLower(strings.TrimSpace(h))
		if strings.Contains(h, "seller") || strings.Contains(h, "receiver") {
			sellerIdx = i
		} else if strings.Contains(h, "buyer") || strings.Contains(h, "sender") {
			buyerIdx = i
		} else if strings.Contains(h, "recording") || strings.Contains(h, "url") {
			urlIdx = i
		} else if strings.Contains(h, "file") && strings.Contains(h, "id") {
			fileIDIdx = i
		}
	}

	if sellerIdx == -1 || buyerIdx == -1 || urlIdx == -1 {
		sellerIdx, buyerIdx, urlIdx = 0, 1, 2
	}

	outFile, err := os.Create(outputPath)
	if err != nil {
		fmt.Printf("Error creating output file %s: %v\n", outputPath, err)
		return
	}
	defer outFile.Close()

	writer := csv.NewWriter(outFile)
	writer.Write([]string{
		"seller id", "Buyer id", "recording_url", "file id", "user details", "mcat_id", "mcat_name", "categories",
		"Thinking JSON", "Thinking summary", "Thinking Input Tokens", "Thinking Output Tokens",
	})
	defer writer.Flush()

	rows := records[1:]
	for i, row := range rows {
		fmt.Printf("[%d/%d] Processing row...\n", i+1, len(rows))
		if len(row) <= max(sellerIdx, max(buyerIdx, urlIdx)) {
			continue
		}

		sellerID := strings.TrimSpace(row[sellerIdx])
		buyerID := strings.TrimSpace(row[buyerIdx])
		recordingURL := strings.TrimSpace(row[urlIdx])
		fileID := ""
		if fileIDIdx != -1 && fileIDIdx < len(row) {
			fileID = strings.TrimSpace(row[fileIDIdx])
		}

		modid := "SELLERMY"
		ak := "eyJ0eXAiOiJKV1QiLCJhbGciOiJzaGEyNTYifQ.eyJpc3MiOiJDUk9OIiwiYXVkIjoiMTAuMjM0LjAuMFwvMTYiLCJleHAiOjE4NzgyODk3MTYsImlhdCI6MTcyMDU4OTcxNiwic3ViIjoiU1VHR0VTVElWRSJ9.3wYDkeV3n0kN1q7qmRFyneuAkWDppKbmTtp5TPeOC9A"
		response, payloadErr := CentralizedPayload(sellerID, buyerID, modid, ak)
		if payloadErr != nil {
			writer.Write([]string{
				sellerID, buyerID, recordingURL, fileID, "{}", "", "", "[]",
				"ERROR", "centralized payload error: " + payloadErr.Error(), "0", "0",
			})
			writer.Flush()
			continue
		}

		buyerDetails, sellerDetails, transactionDetails := processJSONData(string(response), buyerID, sellerID)

		userDetails := map[string]string{
			"buyer_name":   getString(buyerDetails["Fname"]),
			"buyer_city":   getString(buyerDetails["City"]),
			"buyer_number": getString(buyerDetails["ContactNumber"]),
			"seller_name":  getString(sellerDetails["Fname"]),
			"seller_city":  getString(sellerDetails["City"]),
		}
		userDetailsJSON, _ := json.Marshal(userDetails)

		mcatID := getString(transactionDetails["mcat_id"])
		mcatName := getString(transactionDetails["mcat_name"])
		mcatIDInt, _ := strconv.Atoi(mcatID)

		var categories []string
		if mcatIDInt != 0 {
			categories = GetAggregateCategories(mcatIDInt)
		}
		categoriesJSON, _ := json.Marshal(categories)

		callRecordingLink := extractSoundURL(recordingURL)
		audioBytes, _, _, _, _, err_down, _ := downloadAudio(callRecordingLink)

		if err_down != nil {
			writer.Write([]string{
				sellerID, buyerID, recordingURL, fileID, string(userDetailsJSON), mcatID, mcatName, string(categoriesJSON),
				"ERROR", err_down.Error(), "0", "0",
			})
			writer.Flush()
			continue
		}

		summaryYes, jsonYes, inTokensYes, outTokensYes, errYes := generate(modelName, audioBytes, categories, userDetails)
		if errYes != nil {
			fmt.Printf("Row %d failed for seller %s buyer %s: %v\n", i+1, sellerID, buyerID, errYes)
			summaryYes = "ERROR: " + errYes.Error()
		}

		writer.Write([]string{
			sellerID, buyerID, recordingURL, fileID,
			string(userDetailsJSON), mcatID, mcatName, string(categoriesJSON),
			jsonYes, summaryYes, strconv.Itoa(inTokensYes), strconv.Itoa(outTokensYes),
		})
		writer.Flush()
	}
	fmt.Println("\nProcess complete.")
}

func sanitizeForPath(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "unknown-model"
	}
	s = strings.ReplaceAll(s, "..", ".")
	replacer := strings.NewReplacer(
		"/", "_",
		"\\", "_",
		":", "_",
		"*", "_",
		"?", "_",
		"\"", "_",
		"<", "_",
		">", "_",
		"|", "_",
	)
	s = replacer.Replace(s)
	return s
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
