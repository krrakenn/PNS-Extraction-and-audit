import os
import time
import requests
import pandas as pd

REDASH_URL = "https://redash.intermesh.net"
REDASH_API_KEY = os.environ.get("REDASH_API_KEY", "QBP8PHGjiX0DZR4YV0rMed9shXs8b3fAwSmySNSG")
DATA_SOURCE_ID = 14
REDASH_HEADERS = {"Authorization": f"Key {REDASH_API_KEY}"}

def fetch_data():
    sql = """
    select file_id,llm_extracted_json from file_extraction_logs
    where file_id IN(825635, 825634, 825633, 825632, 825631, 825630, 825628, 825629, 825627, 825626, 825625, 825624, 825623, 825622, 825621, 825619, 825620, 825618, 825617, 825616, 825615, 825614, 825613, 825612, 825611, 825610, 825608, 825609, 825607, 825604, 825605, 825606, 825603, 825602, 825600, 825599, 825601, 825598, 825597, 825594, 825595, 825596, 825593, 825584, 825592, 825591, 825590, 825589, 825588, 825587, 825586)
    order by file_id desc;
    """

    print("Submitting query to Redash...")
    response = requests.post(
        f"{REDASH_URL}/api/query_results",
        headers=REDASH_HEADERS,
        json={"query": sql, "data_source_id": DATA_SOURCE_ID, "max_age": 0},
        timeout=30
    )
    response.raise_for_status()
    data = response.json()

    if "job" in data:
        job_id = data["job"]["id"]
        retries = 0
        print(f"Query submitted as job {job_id}, waiting...")
        while retries < 120:
            jr = requests.get(f"{REDASH_URL}/api/jobs/{job_id}", headers=REDASH_HEADERS, timeout=10)
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
    final = requests.get(f"{REDASH_URL}/api/query_results/{query_result_id}.json", headers=REDASH_HEADERS, timeout=30)
    final.raise_for_status()
    
    rows = final.json()["query_result"]["data"]["rows"]
    df = pd.DataFrame(rows)
    print(f"✅ Got {len(df)} rows from Redash")
    
    output_dir = "csv_batch_tool/input"
    os.makedirs(output_dir, exist_ok=True)
    
    # Save to CSV
    output_path = os.path.join(output_dir, "daily_input.csv")
    df.to_csv(output_path, index=False)
    print(f"Saved data to {output_path}")

if __name__ == "__main__":
    fetch_data()
