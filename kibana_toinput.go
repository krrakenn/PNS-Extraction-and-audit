package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func main() {
	inputPath := flag.String("input", "", "Path to input CSV file")
	outputPath := flag.String("output", filepath.Join("csv_batch_tool", "input", "filtered_output_20-04.csv"), "Path to output CSV file")
	keepCols := flag.String("keep", "", "Comma-separated column names to keep (e.g. col1,col2,col3)")
	flag.Parse()

	if strings.TrimSpace(*inputPath) == "" || strings.TrimSpace(*keepCols) == "" {
		fmt.Fprintf(os.Stderr, "Usage: go run kibana_toinput.go -input input.csv -output output.csv -keep col1,col2\n")
		os.Exit(1)
	}

	columnsToKeep := parseColumns(*keepCols)
	if len(columnsToKeep) == 0 {
		fmt.Fprintln(os.Stderr, "No valid columns provided in -keep")
		os.Exit(1)
	}

	if err := filterCSV(*inputPath, *outputPath, columnsToKeep); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Filtered CSV written to %s\n", *outputPath)
}

func parseColumns(raw string) []string {
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		c := strings.TrimSpace(p)
		if c != "" {
			out = append(out, c)
		}
	}
	return out
}

func filterCSV(inputPath, outputPath string, keepColumns []string) error {
	inFile, err := os.Open(inputPath)
	if err != nil {
		return fmt.Errorf("open input CSV: %w", err)
	}
	defer inFile.Close()

	reader := csv.NewReader(inFile)

	header, err := reader.Read()
	if err != nil {
		return fmt.Errorf("read header: %w", err)
	}

	headerIndex := make(map[string]int, len(header))
	for i, name := range header {
		headerIndex[strings.TrimSpace(name)] = i
	}

	selectedIndexes := make([]int, 0, len(keepColumns))
	selectedHeader := make([]string, 0, len(keepColumns))
	for _, col := range keepColumns {
		idx, ok := headerIndex[col]
		if !ok {
			return fmt.Errorf("column not found in CSV header: %s", col)
		}
		selectedIndexes = append(selectedIndexes, idx)
		selectedHeader = append(selectedHeader, col)
	}

	if err := os.MkdirAll(filepath.Dir(outputPath), 0o755); err != nil {
		return fmt.Errorf("create output directory: %w", err)
	}

	outFile, err := os.Create(outputPath)
	if err != nil {
		return fmt.Errorf("create output CSV: %w", err)
	}
	defer outFile.Close()

	writer := csv.NewWriter(outFile)
	defer writer.Flush()

	if err := writer.Write(selectedHeader); err != nil {
		return fmt.Errorf("write output header: %w", err)
	}

	for {
		record, err := reader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fmt.Errorf("read record: %w", err)
		}

		filtered := make([]string, len(selectedIndexes))
		for i, idx := range selectedIndexes {
			if idx < len(record) {
				filtered[i] = record[idx]
			} else {
				filtered[i] = ""
			}
		}

		if err := writer.Write(filtered); err != nil {
			return fmt.Errorf("write filtered record: %w", err)
		}
	}

	if err := writer.Error(); err != nil {
		return fmt.Errorf("flush output CSV: %w", err)
	}

	return nil
}
