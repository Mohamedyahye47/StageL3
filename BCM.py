import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import pandas as pd
from datetime import datetime, timedelta

# srape un jour
def scrape_bcm_day(date_str: str) -> pd.DataFrame:
    """
    Récupère le tableau des taux de change pour une date donnée (YYYY-MM-DD).
    Retourne un DataFrame Pandas.
    """
    url = f"https://www.bcm.mr/money-rate-table?date={date_str}"

    # configurer Chrome
    options = uc.ChromeOptions()
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    driver = uc.Chrome(version_main=138, options=options)

    driver.get(url)
    try:
        # attendre que le tableau charge
        table = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "table"))
        )

        # entêtes
        headers = [th.text for th in table.find_elements(By.TAG_NAME, "th")]

        # lignes
        rows = table.find_elements(By.TAG_NAME, "tr")
        all_data = []
        for row in rows:
            cols = [c.text for c in row.find_elements(By.TAG_NAME, "td")]
            if cols:
                all_data.append(cols)

        df = pd.DataFrame(all_data, columns=headers)
        df["Date"] = date_str

    except Exception as e:
        print(f"⚠️ Pas de données pour {date_str} ({e})")
        df = pd.DataFrame()

    driver.quit()
    return df



# srape une plage de dates
def scrape_bcm_range(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Récupère les taux de change de la BCM entre deux dates incluses.
    start_date et end_date au format 'YYYY-MM-DD'.
    """
    all_data = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        df_day = scrape_bcm_day(date_str)
        if not df_day.empty:
            all_data.append(df_day)
        current += timedelta(days=1)

    if all_data:
        df_final = pd.concat(all_data, ignore_index=True)
    else:
        df_final = pd.DataFrame()

    return df_final
