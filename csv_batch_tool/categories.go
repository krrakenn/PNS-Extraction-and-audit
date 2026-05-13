package main

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
)

const MergedMcatFile = "merged_mcats_dropped_noname.csv"

func InitializeCategoryData() error {
	// Only verify file existence at startup
	if _, err := os.Stat(MergedMcatFile); os.IsNotExist(err) {
		return fmt.Errorf("file not found: %s", MergedMcatFile)
	}
	fmt.Println("Category data verified. Using Zero-RAM disk scanning mode.")
	return nil
}

// GetAggregateCategories: Scans the file on-demand to find matching McatID
func GetAggregateCategories(mcatID int) []string {
	var results []string
	if mcatID == 0 {
		return results
	}

	f, err := os.Open(MergedMcatFile)
	if err != nil {
		return results
	}
	defer f.Close()

	reader := csv.NewReader(f)
	reader.LazyQuotes = true
	reader.Read() // Skip header

	for {
		record, err := reader.Read()
		if err != nil {
			break
		}
		if len(record) < 3 {
			continue
		}

		id, _ := strconv.Atoi(record[0])
		if id == mcatID {
			json.Unmarshal([]byte(record[2]), &results)
			break
		}
	}
	return results
}

// GetIDByName: Scans the file on-demand to find matching Category Name
func GetIDByName(name string) int {
	targetName := strings.TrimSpace(strings.ToLower(name))
	if targetName == "" {
		return 0
	}

	f, err := os.Open(MergedMcatFile)
	if err != nil {
		return 0
	}
	defer f.Close()

	reader := csv.NewReader(f)
	reader.LazyQuotes = true
	reader.Read() // Skip header

	for {
		record, err := reader.Read()
		if err != nil {
			break
		}
		if len(record) < 2 {
			continue
		}

		// record[1] is mcat_name
		if strings.TrimSpace(strings.ToLower(record[1])) == targetName {
			id, _ := strconv.Atoi(record[0])
			return id
		}
	}
	return 0
}