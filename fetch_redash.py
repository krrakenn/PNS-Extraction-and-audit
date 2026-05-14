import os
import time
import requests
import pandas as pd

REDASH_URL = "https://redash.intermesh.net"
REDASH_API_KEY = os.environ.get("REDASH_API_KEY", "")
DATA_SOURCE_ID = 14
REDASH_HEADERS = {"Authorization": f"Key {REDASH_API_KEY}"}

SQL = """
SELECT 
    fel.file_id,
    fel.llm_extracted_json,
    f.file_url,

    MAX(CASE 
        WHEN p.user_role = 'SELLER' 
        THEN p.user_glid 
    END) AS seller_glid,

    MAX(CASE 
        WHEN p.user_role = 'BUYER' 
        THEN p.user_glid 
    END) AS buyer_glid

FROM file_extraction_logs fel
JOIN files f 
    ON f.id = fel.file_id

LEFT JOIN participants p 
    ON p.file_id = fel.file_id
   AND p.is_active = true

WHERE fel.created_at >= CURRENT_DATE - INTERVAL '1 day'
  AND fel.created_at < CURRENT_DATE
  AND fel.is_active = true
  AND f.is_active = true

GROUP BY 
    fel.file_id,
    fel.llm_extracted_json,
    f.file_url,
    fel.created_at

ORDER BY fel.created_at DESC
LIMIT 10;
"""


def fetch_data():
    print("Submitting query to Redash...")
    response = requests.post(
        f"{REDASH_URL}/api/query_results",
        headers=REDASH_HEADERS,
        json={"query": SQL, "data_source_id": DATA_SOURCE_ID, "max_age": 0},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if "job" in data:
        job_id = data["job"]["id"]
        retries = 0
        print(f"Query submitted as job {job_id}, waiting...")
        while retries < 120:
            jr = requests.get(
                f"{REDASH_URL}/api/jobs/{job_id}", headers=REDASH_HEADERS, timeout=10
            )
            jr.raise_for_status()
            jd = jr.json()
            status = jd["job"]["status"]
            if status == 3:
                query_result_id = jd["job"]["query_result_id"]
                break
            elif status == 4:
                raise Exception(jd["job"].get("error", "Query failed"))
            time.sleep(1)
            retries += 1
        else:
            raise Exception("Query execution timeout (120s)")
    else:
        query_result_id = data["query_result"]["id"]

    print(f"Fetching results for query_result_id: {query_result_id}")
    final = requests.get(
        f"{REDASH_URL}/api/query_results/{query_result_id}.json",
        headers=REDASH_HEADERS,
        timeout=30,
    )
    final.raise_for_status()

    rows = final.json()["query_result"]["data"]["rows"]
    df = pd.DataFrame(rows)
    print(f"Got {len(df)} rows from Redash")

    # Rename columns to match what the Go tool and import_flash_data.py expect
    df.rename(
        columns={
            "file_id":            "FILE_ID",
            "llm_extracted_json": "llm_extracted_json",  # kept as-is for import_flash_data.py
            "file_url":           "RECORDING_URL",
            "seller_glid":        "RECEIVER_GLID",  # seller == receiver in Centralized API terms
            "buyer_glid":         "SENDER_GLID",    # buyer  == sender
        },
        inplace=True,
    )

    output_dir = "csv_batch_tool/input"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "daily_input.csv")
    df.to_csv(output_path, index=False)
    print(f"✓ Saved {len(df)} rows → {output_path}")


if __name__ == "__main__":
    fetch_data()
