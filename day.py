from dotenv import load_dotenv
load_dotenv()

import os
import sys
import time
import json
import logging
import traceback
from datetime import datetime, timedelta
import atexit
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from nas_backup import save_json_with_backup
from selenium.common.exceptions import (
    WebDriverException,
    NoSuchElementException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    TimeoutException
)
import atexit
import threading
import platform
import tempfile
import hashlib
import re

# 환경 변수 및 설정
API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
NAS_FOLDER = os.environ.get("NAS_FOLDER")
if not NAS_FOLDER:
    raise RuntimeError("환경변수 NAS_FOLDER가 필요합니다. 예: E:\\ 또는 \\server\\share\\DAY")
RESTORE_AFTER_SCRAPE = os.environ.get("RESTORE_AFTER_SCRAPE", "0") == "1"
CHROME_BIN = os.environ.get("CHROME_BIN", r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", r"C:\\Chrome140\\driver\\chromedriver.exe")
DAYS_TO_FETCH = 1
LOG_FILE = "camfit_booking_table.log"
STATUS_FILE = "day_status.json"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(message)s"
)
def log_info(msg): logging.info(msg)
def log_error(msg): logging.error(msg)
def log_debug(msg): logging.debug(msg)

# Try to reuse shared constants/helpers from camfit_combined when available
try:
    import camfit_combined as _cc
    SPINNER_SELECTORS = getattr(_cc, 'SPINNER_SELECTORS', [".spinner-border"])
    EMPTY_TEXT_PATTERNS = getattr(_cc, 'EMPTY_TEXT_PATTERNS', ["예약이 없습니다", "데이터 없음"])
    MAX_WAIT_FOR_NEXT_DAY = getattr(_cc, 'MAX_WAIT_FOR_NEXT_DAY', 60)
    STABLE_CHECKS = getattr(_cc, 'STABLE_CHECKS', 2)
    STABLE_INTERVAL = getattr(_cc, 'STABLE_INTERVAL', 1.2)
except Exception:
    SPINNER_SELECTORS = [".spinner-border"]
    EMPTY_TEXT_PATTERNS = ["예약이 없습니다", "데이터 없음"]
    MAX_WAIT_FOR_NEXT_DAY = 60
    STABLE_CHECKS = 2
    STABLE_INTERVAL = 1.2


# 상태 관리
_status_lock = threading.Lock()
_status = {
    "status": "idle",
    "started_at": None,
    "ended_at": None,
    "processed_dates": [],
    "error": None
}

def set_status_running():
    with _status_lock:
        _status["status"] = "running"
        _status["started_at"] = datetime.utcnow().isoformat()
        _status["ended_at"] = None
        _status["processed_dates"] = []
        _status["error"] = None
    write_status()

def set_status_finished():
    with _status_lock:
        _status["status"] = "finished"
        _status["ended_at"] = datetime.utcnow().isoformat()
    write_status()

def set_status_error(msg):
    with _status_lock:
        _status["status"] = "error"
        _status["error"] = msg
        _status["ended_at"] = datetime.utcnow().isoformat()
    write_status()

def write_status():
    try:
        with _status_lock:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(_status, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error(f"상태 파일 기록 실패: {e}")

def append_processed(date_str):
    with _status_lock:
        if date_str not in _status["processed_dates"]:
            _status["processed_dates"].append(date_str)
    write_status()

def save_empty_day(date_for_filename):
    save_to_files(
        headers=[
            "사이트","상태","고객명","연락처",
            "예약 인원","차량","예약일",
            "현장결제 금액","선결제 금액","총 이용료","관리메모","요청사항"
        ],
        bookings=[],
        top_summary={
            "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
            "display_date": date_for_filename,
            "booked_count": 0,
            "total_count": 0,
            "percent": 0,
            "summary": {'체크인':0,'체크아웃':0,'이용중':0,'예약불가':0,'공실':0}
        },
        footer_info="",
        stats_info={},
        output_folder=None,
        date_for_filename=date_for_filename,
        prev_day_map=None
    )

def process_today_sheet(driver, wait, today, prev_day_date, prev_day_map):
    date_str = today.strftime("%Y-%m-%d")
    try:
        log_info(f"[day.py] 예약표 추출 시작: {date_str}")
        # 시뮬레이트 모드: 환경변수 SIMULATE_SCRAPE=1 이면 실제 스크랩 대신 더미 데이터를 업로드
        if os.environ.get("SIMULATE_SCRAPE", "0") == "1":
            log_info("SIMULATE_SCRAPE enabled: uploading sample rows instead of scraping")
            sample_rows = [
                {
                    "사이트": "SIM_TEST",
                    "상태": "체크인",
                    "고객명": "테스트 고객",
                    "연락처": "010-0000-0000",
                    "예약 인원": "2",
                    "차량": "",
                    "예약일": date_str,
                    "현장결제 금액": "0",
                    "선결제 금액": "0",
                    "총 이용료": "0",
                    "관리메모": ["시뮬레이션"] ,
                    "요청사항": ""
                }
            ]
            save_to_files(
                headers=[
                    "사이트","상태","고객명","연락처",
                    "예약 인원","차량","예약일",
                    "현장결제 금액","선결제 금액","총 이용료","관리메모","요청사항"
                ],
                bookings=sample_rows,
                top_summary={
                    "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
                    "display_date": date_str,
                    "booked_count": 1,
                    "total_count": 1,
                    "percent": 100,
                    "summary": {'체크인':1,'체크아웃':0,'이용중':0,'예약불가':0,'공실':0}
                },
                footer_info="SIMULATED",
                stats_info={},
                output_folder=None,
                date_for_filename=date_str,
                prev_day_map=prev_day_map,
                option_cols=None
            )
            return
        # 실제 스크래핑 로직이 비어 있으면 진단 및 재시도 로직을 넣어 업로드 전에 검증합니다.
        # 드라이버가 세팅되어 있지 않으면 바로 빈 업로드로 처리
        if not driver:
            log_error("WebDriver 미설정: 실제 스크래핑을 시도할 수 없습니다. 빈 예약표 업로드 예정.")
            save_empty_day(date_str)
            return

        # 시도 1: 브라우저 내 fetch를 사용하여 API에서 바로 예약정보를 가져와 본다 (SPA가 사용하는 엔드포인트)
        prev_sig = None
        try:
            bf_headers, bf_bookings = browser_fetch_bookings(driver, date_str)
            if bf_bookings:
                log_info(f"{date_str} browser_fetch_bookings succeeded bookings_count={len(bf_bookings)}")
                save_to_files(headers=bf_headers or [
                    "사이트","상태","고객명","연락처",
                    "예약 인원","차량","예약일",
                    "현장결제 금액","선결제 금액","총 이용료","관리메모","요청사항"
                ], bookings=bf_bookings, top_summary={"updated_at": datetime.now().strftime("%Y/%m/%d %H:%M"), "display_date": date_str, "booked_count": len(bf_bookings), "total_count": len(bf_bookings), "percent": 100, "summary": {}}, footer_info="", stats_info={}, output_folder=None, date_for_filename=date_str, prev_day_map=prev_day_map)
                return
        except Exception as e:
            log_error(f"browser_fetch_bookings 예외: {e}")

        # 시도 2: 현재 페이지에서 테이블 추출 시도
        status, headers, bookings = wait_for_next_day_table(driver, date_str, prev_sig)
        if status == "ok" and bookings:
            save_to_files(headers=headers, bookings=bookings, top_summary=extract_top_summary(driver), footer_info=extract_footer_info(driver), stats_info={}, output_folder=None, date_for_filename=date_str, prev_day_map=prev_day_map)
            return

        # 빈 데이터 또는 실패인 경우: 상세 진단을 남기고 1회 재시도
        log_error(f"{date_str} 첫 번째 추출 결과: {status} bookings_count={len(bookings) if bookings else 0}")
        try:
            log_dom_diagnostics(driver, date_str, prefix="first")
        except Exception as e:
            log_error(f"도메인 진단 실패: {e}")

        # 짧은 대기 후 재시도
        time.sleep(5)
        status2, headers2, bookings2 = wait_for_next_day_table(driver, date_str, prev_sig)
        if status2 == "ok" and bookings2:
            save_to_files(headers=headers2, bookings=bookings2, top_summary=extract_top_summary(driver), footer_info=extract_footer_info(driver), stats_info={}, output_folder=None, date_for_filename=date_str, prev_day_map=prev_day_map)
            return

        # 두 번째 시도도 실패하면 진단을 남기고 빈 업로드 수행
        log_error(f"{date_str} 두 번째 추출 결과: {status2} bookings_count={len(bookings2) if bookings2 else 0}")
        try:
            log_dom_diagnostics(driver, date_str, prefix="second")
        except Exception as e:
            log_error(f"도메인 진단 실패(재시도 후): {e}")

        save_empty_day(date_str)
    except Exception as e:
        log_error(f"[day.py] {date_str} 추출 실패: {e}")
        traceback.print_exc()

def process_next_day_sheet(driver, wait, today, prev_day_map):
    next_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        # ... 내일 예약표 추출 및 저장 로직 ...
        save_empty_day(next_date)  # 예시: 실제 로직은 필요에 따라 구현
    except Exception as e:
        log_error(f"[day.py] 내일 예약표 추출 실패: {e}")
        traceback.print_exc()


# --------------------------------------------------------------------------------
# 토글
# --------------------------------------------------------------------------------
def ensure_named_toggles_checked(driver, wait, names_to_check):
    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.row > div > button")))
    except TimeoutException:
        log_error("토글 로딩 실패")
        return
    time.sleep(2)
    buttons = driver.find_elements(By.CSS_SELECTOR, "div.row > div > button")
    text_to_btn = {}
    for b in buttons:
        try:
            text_to_btn[b.text.strip()] = b
        except:
            pass
    for name in names_to_check:
        btn = text_to_btn.get(name)
        if not btn:
            log_error(f"'{name}' 버튼 없음")
            continue
        if "btn-primary" not in btn.get_attribute("class"):
            try:
                driver.execute_script("arguments[0].click();", btn)
                _wait_spinners(driver, max_wait=10)
                log_info(f"{name} 토글 ON")
            except Exception as e:
                log_error(f"{name} 클릭 실패: {e}")
    time.sleep(2)

# --------------------------------------------------------------------------------
# 예약표 추출 함수 (헤더/rows/메타 등)
# --------------------------------------------------------------------------------
def extract_top_summary(driver):
    date_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    container = driver.find_element(By.CSS_SELECTOR, ".container-fluid.d-flex.justify-content-between")
    divs = container.find_elements(By.XPATH, "./div")
    left, right = divs[0], divs[1]
    site_count_elem = left.find_element(By.CSS_SELECTOR, "h4.font-weight-normal")
    txt = site_count_elem.text.strip() or site_count_elem.get_attribute("innerText").strip()
    txt = txt.replace("개", "")
    m = re.search(r'(\d+)\s*/\s*(\d+)\s*\((\d+)%\)', txt)
    if not m:
        raise ValueError(f"상단 수치 패턴 불일치: {txt}")
    booked_count, total_count, percent = map(int, m.groups())
    date_label = right.find_element(By.CSS_SELECTOR, "h4.noble-ui-logo").text.strip()
    status = { '체크인':0, '체크아웃':0, '이용중':0, '예약불가':0, '공실':0 }
    for p in driver.find_elements(By.CSS_SELECTOR, "p.text-right.mb-1"):
        t = p.text.strip()
        if "체크인" in t: status['체크인'] = _safe_int(p)
        elif "체크아웃" in t: status['체크아웃'] = _safe_int(p)
        elif "이용중" in t: status['이용중'] = _safe_int(p)
        elif "예약불가" in t: status['예약불가'] = _safe_int(p)
        elif "공실" in t: status['공실'] = _safe_int(p)
    return {
        "updated_at": date_time,
        "display_date": date_label,
        "booked_count": booked_count,
        "total_count": total_count,
        "percent": percent,
        "summary": status
    }

def _safe_int(p_elem):
    try:
        return int(p_elem.find_element(By.TAG_NAME, "b").text.strip())
    except:
        return 0

def extract_reservation_data(driver):
    table = driver.find_element(By.CSS_SELECTOR, "table.table-bordered")
    tbody = table.find_element(By.TAG_NAME, "tbody")
    headers = [
        "사이트","상태","고객명","연락처",
        "예약 인원","차량","예약일",
        "현장결제 금액","선결제 금액","총 이용료","관리메모","요청사항"
    ]
    rows = []
    tr_list = tbody.find_elements(By.TAG_NAME, "tr")
    i = 0
    while i < len(tr_list):
        tr = tr_list[i]
        tds = tr.find_elements(By.TAG_NAME, "td")
        if len(tds) >= 11:
            raw_site = tds[0].text.strip()
            site = raw_site.split('>')[-1].replace("애견존","").strip() if '>' in raw_site else raw_site.replace("애견존","").strip()
            texts = [td.text.replace("포함","").strip() for td in tds]
            status_raw = texts[1]
            if "이용중" in status_raw: status="이용중"
            elif "체크인" in status_raw: status="체크인"
            elif "체크아웃" in status_raw: status="체크아웃"
            else: status=""
            row = {
                "사이트": site,
                "상태": status,
                "고객명": texts[2],
                "연락처": texts[3],
                "예약 인원": texts[4],
                "차량": texts[5],
                "예약일": texts[6],
                "현장결제 금액": texts[7],
                "선결제 금액": texts[8],
                "총 이용료": texts[10],
                "관리메모": [],
                "요청사항": ""
            }
            j = i+1
            while j < len(tr_list):
                next_tr = tr_list[j]
                next_tds = next_tr.find_elements(By.TAG_NAME, "td")
                if len(next_tds) >= 3 and next_tr.get_attribute("class") and 'text-muted' in next_tr.get_attribute("class"):
                    label = next_tds[1].text.strip()
                    value = next_tds[2].text.strip()
                    if "관리메모" in label:
                        # 관리메모는 항상 리스트로 변환
                        if isinstance(value, list):
                            row["관리메모"] = [str(v).strip() for v in value if str(v).strip()]
                        elif value is None or value == "":
                            row["관리메모"] = []
                        else:
                            # 여러 줄 메모는 줄바꿈 기준 분리
                            row["관리메모"] = [s.strip() for s in str(value).split("\n") if s.strip()]
                    elif "요청사항" in label:
                        row["요청사항"] = value
                    else:
                        break
                    j += 1
                else:
                    break
            i = j - 1
            rows.append(row)
        i += 1
    return headers, rows

def extract_footer_info(driver):
    try:
        table = driver.find_element(By.CSS_SELECTOR, "table.table-bordered")
        parent = table.find_element(By.XPATH, "..")
        siblings = parent.find_elements(By.XPATH, "./*")
        found = False
        for sib in siblings:
            if sib == table:
                found = True
                continue

            # ...existing code...
        return stats
    except Exception as e:
        log_error(f"통계 정보 추출 실패: {e}")
        return {}

# --------------------------------------------------------------------------------
# Spinner Wait
# --------------------------------------------------------------------------------
def _any_spinner_present(driver):
    for sel in SPINNER_SELECTORS:
        try:
            driver.find_element(By.CSS_SELECTOR, sel)
            return True
        except NoSuchElementException:
            continue
    return False

def _wait_spinners(driver, max_wait=30):
    end = time.time() + max_wait
    while time.time() < end:
        if not _any_spinner_present(driver):
            return True
        time.sleep(2)
    return False


def log_dom_diagnostics(driver, date_str, prefix="diag"):
    """Save diagnostic artifacts (page source, screenshot, summary json) to NAS_FOLDER for debugging.
    Files: {prefix}_dom_{date}.html/.png/.json
    """
    try:
        folder = NAS_FOLDER or os.getcwd()
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, f"{prefix}_dom_{date_str}")
        html_path = base + ".html"
        png_path = base + ".png"
        json_path = base + ".json"
        try:
            src = driver.page_source
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(src)
        except Exception as e:
            log_error(f"DOM HTML 저장 실패: {e}")
        try:
            # Selenium API: save_screenshot is robust across drivers
            driver.save_screenshot(png_path)
        except Exception as e:
            log_error(f"스크린샷 저장 실패: {e}")
        summary = {"url": None, "title": None, "body_snippet": None, "table_count": 0}
        try:
            summary["url"] = driver.current_url
            try:
                summary["title"] = driver.title
            except Exception:
                summary["title"] = None
            try:
                body = driver.find_element(By.TAG_NAME, "body").text
                summary["body_snippet"] = body[:500]
            except Exception:
                summary["body_snippet"] = None
            try:
                tables = driver.find_elements(By.CSS_SELECTOR, "table")
                summary["table_count"] = len(tables)
            except Exception:
                summary["table_count"] = 0
        except Exception as e:
            log_error(f"DOM 요약 수집 실패: {e}")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_error(f"DOM 요약 저장 실패: {e}")
        log_info(f"DOM 진단 저장: {html_path}, {png_path}, {json_path}")
    except Exception as e:
        log_error(f"log_dom_diagnostics 실패: {e}")

def _get_table_and_rows(driver):
    try:
        tbl = driver.find_element(By.CSS_SELECTOR, "table.table-bordered")
        tbody = tbl.find_element(By.TAG_NAME, "tbody")
        rows = tbody.find_elements(By.TAG_NAME, "tr")
        return tbl, rows
    except (NoSuchElementException, StaleElementReferenceException):
        return None, None

def _table_signature(tbl):
    try:
        tbody = tbl.find_element(By.TAG_NAME, "tbody")
        txt = tbody.text.strip()
        return hashlib.sha256(txt.encode("utf-8")).hexdigest()
    except:
        return None

def _body_text_lower(driver):
    try:
        return driver.find_element(By.TAG_NAME, "body").text.lower()
    except:
        return ""

def click_next_day_button(driver, wait):
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "button.btn.btn-primary.float-right")
        for b in btns:
            try:
                b.find_element(By.CSS_SELECTOR, "i.feather.icon-arrow-right")
                if b.is_enabled() and b.is_displayed():
                    try:
                        b.click()
                    except ElementClickInterceptedException:
                        driver.execute_script("arguments[0].click();", b)
                    log_info("다음 날짜 버튼 클릭")
                    return True
            except:
                continue
        return False
    except:
        return False

def wait_for_next_day_table(driver, target_date, prev_table_sig):
    deadline = time.time() + MAX_WAIT_FOR_NEXT_DAY
    last_row_count = None
    stable_count = 0
    last_sig = None
    while time.time() < deadline:
        if _any_spinner_present(driver):
            time.sleep(2); continue
        tbl, rows = _get_table_and_rows(driver)
        if tbl:
            sig = _table_signature(tbl)
            if sig == prev_table_sig:
                time.sleep(2); continue
            row_len = len(rows) if rows else 0
            body_txt = _body_text_lower(driver)
            if row_len == 0 and any(pat in body_txt for pat in EMPTY_TEXT_PATTERNS):
                log_info(f"{target_date} 빈 데이터 감지")
                return "empty", None, None
            if last_row_count is None:
                last_row_count = row_len
                stable_count = 1
                last_sig = sig
            else:
                if row_len == last_row_count and sig == last_sig:
                    stable_count += 1
                else:
                    last_row_count = row_len
                    stable_count = 1
                    last_sig = sig
            if stable_count >= STABLE_CHECKS:
                try:
                    headers, bookings = extract_reservation_data(driver)
                    return "ok", headers, bookings
                except Exception as e:
                    log_error(f"표 추출 실패: {e}")
                    return "fail", None, None
            time.sleep(STABLE_INTERVAL)
            continue
        else:
            body_txt = _body_text_lower(driver)
            if any(pat in body_txt for pat in EMPTY_TEXT_PATTERNS):
                log_info(f"{target_date} 빈 데이터 패턴 감지")
                return "empty", None, None
        time.sleep(2)
    log_error(f"{target_date} 테이블 로드 타임아웃")
    return "fail", None, None
    
def normalize_memo(value):
    if value is None or str(value).strip() == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if "\n" in str(value):
        return [s.strip() for s in str(value).split("\n") if s.strip()]
    return [str(value).strip()]


def normalize_bookings(bookings):
    """Normalize a list of booking row dicts in-place to match API expectations.
    - 관리메모: list
    - 같이온사이트: list of strings
    - numeric/money fields -> strings
    - ensure key presence for common fields
    Returns the normalized bookings list (same object).
    """
    if bookings is None:
        return []

    # operate in-place for existing list
    for row in bookings:
        if not isinstance(row, dict):
            continue

        # 관리메모
        try:
            row['관리메모'] = normalize_memo(row.get('관리메모', ''))
        except Exception:
            row['관리메모'] = []

        # 같이온사이트: ensure list of non-empty strings
        gs = row.get('같이온사이트')
        if gs is None:
            row['같이온사이트'] = []
        elif isinstance(gs, list):
            try:
                row['같이온사이트'] = [str(x).strip() for x in gs if str(x).strip()]
            except Exception:
                row['같이온사이트'] = []
        else:
            s = str(gs).strip()
            row['같이온사이트'] = [s] if s else []

        # 금액/숫자 필드를 문자열로 강제 (API는 문자열 타입을 기대)
        for k in ["총 이용료", "현장결제 금액", "선결제 금액", "예약 인원"]:
            v = row.get(k)
            if v is None:
                row[k] = ""
            elif not isinstance(v, str):
                try:
                    row[k] = str(v)
                except Exception:
                    row[k] = ""

        # 기타 표시 필드들도 문자열로 보장
        for k in ["연락처", "예약일", "사이트", "고객명", "차량", "요청사항"]:
            v = row.get(k)
            if v is None:
                row[k] = ""
            elif not isinstance(v, str):
                try:
                    row[k] = str(v)
                except Exception:
                    row[k] = ""

    return bookings

# --------------------------------------------------------------------------------
# API 업로드 함수 (DB 저장)
# --------------------------------------------------------------------------------
def push_sheet_to_api(date_str, top, headers, stats, bookings, option_cols=None, version=None):
    # bookings의 모든 row의 관리메모를 배열로 변환!
    for row in bookings:
        # normalize 관리메모 -> 항상 리스트
        row["관리메모"] = normalize_memo(row.get("관리메모", ""))

        # 같이온사이트: 리스트로 보장
        gs = row.get("같이온사이트")
        if gs is None:
            row["같이온사이트"] = []
        elif isinstance(gs, list):
            try:
                row["같이온사이트"] = [str(x) for x in gs if str(x).strip()]
            except Exception:
                row["같이온사이트"] = []
        else:
            s = str(gs).strip()
            row["같이온사이트"] = [s] if s else []

        # 금액/숫자 필드를 문자열로 강제 (API는 문자열 타입을 기대)
        for k in ["총 이용료", "현장결제 금액", "선결제 금액", "예약 인원"]:
            v = row.get(k)
            if v is None:
                row[k] = ""
            elif not isinstance(v, str):
                try:
                    row[k] = str(v)
                except Exception:
                    row[k] = ""

        # 기타 표시 필드들도 문자열로 보장
        for k in ["연락처", "예약일", "사이트", "고객명", "차량", "요청사항"]:
            v = row.get(k)
            if v is None:
                row[k] = ""
            elif not isinstance(v, str):
                try:
                    row[k] = str(v)
                except Exception:
                    row[k] = ""
    # 디버그: 업로드 직전에 페이로드/행 수를 로그에 남기고 파일로 저장
    try:
        sample = json.dumps(bookings[:3], ensure_ascii=False)
    except Exception:
        sample = str(bookings[:3])
    log_info(f"[day.py] push_sheet_to_api date={date_str} bookings_count={len(bookings)} sample={sample[:1000]}")
    try:
        payload_preview = {
            "date": date_str,
            "version": version if version is not None else 0,
            "sheet_preview": bookings[:5]
        }
        # ensure /app directory exists (on host or container) before writing
        try:
            os.makedirs('/app', exist_ok=True)
        except Exception:
            pass
        with open(f"/app/last_payload_{date_str}.json", "w", encoding="utf-8") as _f:
            json.dump(payload_preview, _f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error(f"last_payload 파일 저장 실패: {e}")
    payload = {
        "date": date_str,
        "version": version if version is not None else 0,
        "top": top,
        "headers": headers,
        "stats": stats,
        "sheet": bookings,
        "optionCols": option_cols or {}
    }
    log_debug(f"클라이언트가 저장 시도하는 버전: {payload['version']}")
    try:
        r = requests.post(
            f"{API_BASE}/api/update-daily-sheet",
            json=payload,
            timeout=20
        )
        # 기록: 서버 응답을 NAS_FOLDER(/app) 아래에 저장하여 문제 진단에 사용
        try:
            out_folder = NAS_FOLDER or os.getcwd()
            os.makedirs(out_folder, exist_ok=True)
            resp_path = os.path.join(out_folder, f"push_response_{date_str}.json")
            try:
                with open(resp_path, 'w', encoding='utf-8') as rf:
                    try:
                        json.dump({ 'status_code': r.status_code, 'text': r.text, 'json': r.json() if r.headers.get('content-type','').startswith('application/json') else None }, rf, ensure_ascii=False, indent=2)
                    except Exception:
                        rf.write(json.dumps({ 'status_code': r.status_code, 'text': r.text }, ensure_ascii=False))
            except Exception as _e:
                log_error(f"서버 응답 파일 저장 실패: {_e}")
        except Exception:
            pass
        if r.status_code == 409:
            # 충돌: 서버의 최신 데이터를 받아와 간단 병합 후 1회 재시도
            server_info = r.json()
            server_ver = server_info.get('current_version')
            log_debug(f"서버의 최신 버전(응답): {server_ver}")
            log_error(f"{date_str} 예약표 업로드 실패(버전 충돌): {r.text}")
            try:
                srv = requests.get(f"{API_BASE}/api/daily-sheet?date={date_str}", timeout=10)
                if srv.ok:
                    srv_json = srv.json()
                    server_sheet = srv_json.get('sheet', [])
                    # 간단 병합 전략: 서버의 row를 우선 사용하고, 클라이언트에만 있는 row는 추가
                    def key_of(row):
                        return f"{row.get('사이트','')}|{row.get('고객명','')}|{row.get('연락처','')}|{row.get('예약일','')}"
                    server_map = {key_of(r): r for r in server_sheet}
                    client_map = {key_of(r): r for r in bookings}
                    merged = []
                    # server first
                    for k, sv in server_map.items():
                        merged.append(sv)
                    # add client-only
                    for k, cv in client_map.items():
                        if k not in server_map:
                            merged.append(cv)
                    payload['sheet'] = merged
                    payload['version'] = server_ver or 0
                    # 재시도
                    rr = requests.post(f"{API_BASE}/api/update-daily-sheet", json=payload, timeout=20)
                    try:
                        merged_resp_path = os.path.join(out_folder, f"push_response_{date_str}_merged.json")
                    except Exception:
                        merged_resp_path = f"push_response_{date_str}_merged.json"
                    try:
                        if rr.ok:
                            try:
                                mr = {'status_code': rr.status_code, 'json': rr.json(), 'text': rr.text}
                            except Exception:
                                mr = {'status_code': rr.status_code, 'text': rr.text}
                            try:
                                with open(merged_resp_path, 'w', encoding='utf-8') as mf:
                                    json.dump(mr, mf, ensure_ascii=False, indent=2)
                            except Exception:
                                pass
                            # overwrite primary push_response to final success
                            try:
                                with open(resp_path, 'w', encoding='utf-8') as rf:
                                    json.dump(mr, rf, ensure_ascii=False, indent=2)
                            except Exception:
                                pass
                            log_info(f"{date_str} 업로드 성공(충돌 병합 후): {rr.json()}")
                            log_info(f"[day.py] {date_str} 업로드 성공(충돌 병합 후)!")
                            return
                        else:
                            try:
                                mr = {'status_code': rr.status_code, 'text': rr.text}
                            except Exception:
                                mr = {'status_code': rr.status_code, 'text': rr.text}
                            try:
                                with open(merged_resp_path, 'w', encoding='utf-8') as mf:
                                    json.dump(mr, mf, ensure_ascii=False, indent=2)
                            except Exception:
                                pass
                            log_error(f"{date_str} 재업로드 실패: {rr.text}")
                            log_error(f"[day.py] {date_str} 재업로드 실패: {rr.text}")
                    except Exception as _ee:
                        log_error(f"재시도 응답 기록 중 오류: {_ee}")
                else:
                    log_error(f"{date_str} 서버 데이터 조회 실패: {srv.status_code}")
            except Exception as e:
                log_error(f"충돌 처리 중 예외: {e}")
            # 재시도도 실패하면 백업
            with open(f"conflict_backup_{date_str}.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            log_info(f"[day.py] {date_str} 충돌 백업 저장됨: conflict_backup_{date_str}.json")
        elif not r.ok:
            log_error(f"{date_str} 예약표 업로드 실패: {r.text}")
            log_error(f"[day.py] {date_str} 업로드 실패: {r.text}")
        else:
            log_info(f"{date_str} 업로드 성공: {r.json()}")
            log_info(f"[day.py] {date_str} 업로드 성공!")
    except Exception as e:
        log_error(f"{date_str} 예약표 업로드 예외: {e}")
        log_error(f"[day.py] {date_str} 업로드 예외: {e}")
        # (선택) 실패시 임시 파일 저장
        with open(f"fail_backup_{date_str}.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

def save_to_files(headers, bookings, top_summary, footer_info, stats_info, output_folder, date_for_filename, prev_day_map, option_cols=None):
    # 서버 버전을 조회해서 항상 최신 version으로 저장 시도!
    version = fetch_sheet_version(date_for_filename)

    # 0) JSON 저장 + 백업(E:\YYYY-MM-DD.json, 기존 있으면 E:\bak\YYYY-MM-DD_HHMMSS.json)
    try:
        payload = _build_payload_for_day(
            date_for_filename,
            top_summary,
            headers,
            stats_info,
            footer_info,
            option_cols or {},
            bookings,
            version or 0
        )
        # ensure saved payload rows are normalized
        try:
            normalize_bookings(payload.get('sheet', []))
        except Exception:
            pass
        save_res = save_json_with_backup(NAS_FOLDER, date_for_filename, payload)
        log_info(f"JSON saved: {save_res.get('saved_to')}, backup: {save_res.get('backed_up_to')}")
    except Exception as e:
        log_error(f"JSON 저장/백업 실패: {e}")

    # 1) API 업로드(DB 저장)
    push_sheet_to_api(
        date_str=date_for_filename,
        top=top_summary,
        headers=headers,
        stats=stats_info,
        bookings=bookings,
        option_cols=option_cols,
        version=version
    )

    # 2) (옵션) JSON으로 강제 복원
    if RESTORE_AFTER_SCRAPE:
        try:
            saved_path = save_res.get("saved_to") if 'save_res' in locals() else os.path.join(NAS_FOLDER, f"{date_for_filename}.json")
            try:
                api_res = _force_restore_via_api(date_for_filename, saved_path)
                log_info(f"Force restore done: {api_res}")
            except Exception as e:
                log_error(f"Force restore 호출 실패: {e}")
        except Exception as e:
            log_error(f"Force restore 실패: {e}")

    append_processed(date_for_filename)

def save_empty_day(date_for_filename):
    # 빈 예약표를 API로 업로드
    save_to_files(
        headers=[
            "사이트","상태","고객명","연락처",
            "예약 인원","차량","예약일",
            "현장결제 금액","선결제 금액","총 이용료","관리메모","요청사항"
        ],
        bookings=[],
        top_summary={
            "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
            "display_date": date_for_filename,
            "booked_count": 0,
            "total_count": 0,
            "percent": 0,
            "summary": {'체크인':0,'체크아웃':0,'이용중':0,'예약불가':0,'공실':0}
        },
        footer_info="",
        stats_info={},
        output_folder=None,
        date_for_filename=date_for_filename,
        prev_day_map=None
    )

# --------------------------------------------------------------------------------
# 기타
# --------------------------------------------------------------------------------
def safe_quit(driver):
    try:
        sys.stderr = open(os.devnull, "w")
        driver.quit()
    except:
        pass
    finally:
        sys.stderr = sys.__stderr__

# --------------------------------------------------------------------------------
# 메인
# --------------------------------------------------------------------------------

def fetch_sheet_version(date_str):
    try:
        r = requests.get(f"{API_BASE}/api/daily-sheet?date={date_str}", timeout=8)
        if r.ok:
            data = r.json()
            return data.get("version", 0)
    except Exception as e:
        log_error(f"{date_str} 버전 조회 실패: {e}")
    return 0


def _build_payload_for_day(date_for_filename, top_summary, headers, stats_info, footer_info, option_cols, bookings, version):
    # Minimal payload builder used by save_to_files; mirrors expected API shape
    try:
        return {
            "date": date_for_filename,
            "version": version,
            "top": top_summary,
            "headers": headers,
            "stats": stats_info,
            "footer": footer_info,
            "optionCols": option_cols,
            "sheet": bookings
        }
    except Exception:
        return {
            "date": date_for_filename,
            "version": version,
            "top": top_summary,
            "headers": headers,
            "stats": stats_info,
            "footer": footer_info,
            "optionCols": option_cols,
            "sheet": bookings
        }


def _force_restore_via_api(date_for_filename, json_path):
    # Best-effort helper: POST to /api/restore-from-json if available (non-fatal)
    try:
        if not os.path.exists(json_path):
            return {"ok": False, "error": "json not found", "path": json_path}
        files = {"file": open(json_path, "rb")}
        r = requests.post(f"{API_BASE}/api/restore-from-json", files=files, timeout=30)
        try:
            return r.json()
        except Exception:
            return {"ok": r.ok, "status_code": r.status_code, "text": r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def main():
    set_status_running()
    driver = None
    wait = None
    try:
        log_info("[day.py] 예약표 수집 시작")
        # 실제 드라이버 생성: camfit_combined의 helper 사용
        try:
            import camfit_combined as cc
            # 강제 헤드리스 모드로 실행(컨테이너 환경 대비)
            try:
                cc.HEADLESS = True
            except Exception:
                pass
            driver = cc.build_driver()
            if driver:
                wait = WebDriverWait(driver, 30)
                # 안전하게 종료되도록 등록
                atexit.register(safe_quit, driver)
                # 로그인 시도
                try:
                    login_id = os.environ.get("CAMFIT_ID")
                    login_pw = os.environ.get("CAMFIT_PW")
                    if not login_id or not login_pw:
                        log_error("환경변수 CAMFIT_ID/CAMFIT_PW 미설정: 로그인하지 않습니다.")
                    else:
                        try:
                            ok = cc.camfit_login(driver, wait, login_id, login_pw)
                            if not ok:
                                log_error("로그인 실패: 실제 스크래핑을 계속하지 않습니다.")
                                try:
                                    log_info("로그인 실패 -> log_dom_diagnostics 호출 전")
                                    # 로그인 실패 시 화면/DOM 덤프
                                    log_dom_diagnostics(driver, datetime.now().strftime("%Y-%m-%d"), prefix="login")
                                    log_info("로그인 실패 -> log_dom_diagnostics 호출 후")
                                except Exception as _e:
                                    log_error(f"로그인 실패 후 DOM 진단 실패: {_e}")
                        except Exception as exc:
                            # unexpected exception from login; log traceback and force diag dump
                            tb = traceback.format_exc()
                            log_error(f"camfit_login 예외 발생: {exc}\n{tb}")
                            # ensure /app exists if possible, and also write to cwd as fallback
                            date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                            trace_name_app = f"/app/camfit_login_trace_{date_str}.txt"
                            trace_name_cwd = os.path.join(os.getcwd(), f"camfit_login_trace_{date_str}.txt")
                            try:
                                try:
                                    os.makedirs('/app', exist_ok=True)
                                except Exception:
                                    pass
                                with open(trace_name_app, 'w', encoding='utf-8') as tf:
                                    tf.write(tb)
                                log_info(f"로그인 예외 스택 저장: {trace_name_app}")
                            except Exception:
                                try:
                                    with open(trace_name_cwd, 'w', encoding='utf-8') as tf:
                                        tf.write(tb)
                                    log_info(f"로그인 예외 스택 저장(대체): {trace_name_cwd}")
                                except Exception as _e:
                                    log_error(f"로그인 예외 스택 저장 실패: {_e}")
                                    # Attempt to capture browser state: current URL, cookies, console logs
                                    try:
                                        url = None
                                        cookies = None
                                        console_logs = None
                                        try:
                                            url = driver.current_url
                                        except Exception:
                                            url = '<no-url>'
                                        try:
                                            cookies = driver.get_cookies()
                                        except Exception:
                                            cookies = None
                                        try:
                                            # not all drivers support get_log; wrap in try
                                            console_logs = None
                                            try:
                                                console_logs = driver.get_log('browser')
                                            except Exception:
                                                console_logs = None
                                        except Exception:
                                            console_logs = None

                                        state = {
                                            'url': url,
                                            'cookies': cookies,
                                            'console_logs_sample': (console_logs[:50] if console_logs and isinstance(console_logs, list) else None)
                                        }
                                        state_path_app = f"/app/camfit_login_state_{date_str}.json"
                                        state_path_cwd = os.path.join(os.getcwd(), f"camfit_login_state_{date_str}.json")
                                        try:
                                            with open(state_path_app, 'w', encoding='utf-8') as sf:
                                                json.dump(state, sf, ensure_ascii=False, indent=2)
                                            log_info(f"로그인 상태 저장: {state_path_app}")
                                        except Exception:
                                            try:
                                                with open(state_path_cwd, 'w', encoding='utf-8') as sf:
                                                    json.dump(state, sf, ensure_ascii=False, indent=2)
                                                log_info(f"로그인 상태 저장(대체): {state_path_cwd}")
                                            except Exception as _e:
                                                log_error(f"로그인 상태 저장 실패: {_e}")
                                    except Exception as _e:
                                        log_error(f"로그인 상태 캡처 실패: {_e}")

                                    try:
                                        log_info("로그인 예외 -> 강제 DOM 진단 호출 전")
                                        log_dom_diagnostics(driver, datetime.now().strftime("%Y-%m-%d"), prefix="login-exception")
                                        log_info("로그인 예외 -> 강제 DOM 진단 호출 후")
                                    except Exception as _e:
                                        log_error(f"로그인 예외 후 DOM 진단 실패: {_e}")
                except Exception as e:
                    log_error(f"로그인 예외: {e}")
        except Exception as e:
            log_error(f"camfit_combined 모듈 로드/드라이버 생성 실패: {e}")
        # continue main flow even if driver/login failed (process_today_sheet will handle missing driver)
        today = datetime.now()
        prev_day_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        prev_day_map = {}
        process_today_sheet(driver, wait, today, prev_day_date, prev_day_map)
        if DAYS_TO_FETCH > 1:
            process_next_day_sheet(driver, wait, today, prev_day_map)
        set_status_finished()
    except Exception as e:
        log_error(f"전체 실행 오류: {e}")
        traceback.print_exc()
        set_status_error(str(e))

if __name__ == "__main__":
    main()


def log_dom_diagnostics(driver, date_str, prefix="diag"):
    """Write simple DOM diagnostics to /app/dom_diag_{date}_{prefix}.json and log counts."""
    try:
        # ensure /app exists
        try:
            os.makedirs('/app', exist_ok=True)
        except Exception:
            pass

        out = {
            "date": date_str,
            "time": datetime.now().isoformat(),
            "body_snippet": None,
            "table_found": False,
            "table_count": 0,
            "table_rows": 0,
            "table_text_hash": None
        }
        try:
            body = driver.find_element(By.TAG_NAME, "body")
            text = body.text.strip()
            out["body_snippet"] = text[:2000]
        except Exception:
            out["body_snippet"] = "<no-body>"

        try:
            tables = driver.find_elements(By.CSS_SELECTOR, "table")
            out["table_count"] = len(tables)
            if len(tables) > 0:
                tbl = tables[0]
                try:
                    tbody = tbl.find_element(By.TAG_NAME, "tbody")
                    rows = tbody.find_elements(By.TAG_NAME, "tr")
                    out["table_found"] = True
                    out["table_rows"] = len(rows)
                    try:
                        out["table_text_hash"] = hashlib.sha256(tbody.text.strip().encode("utf-8")).hexdigest()
                    except Exception:
                        out["table_text_hash"] = None
                except Exception:
                    # fallback: count tr under table
                    try:
                        rows = tbl.find_elements(By.TAG_NAME, "tr")
                        out["table_rows"] = len(rows)
                        out["table_found"] = True if len(rows) > 0 else out["table_found"]
                    except Exception:
                        pass
        except Exception:
            pass

        # write diag json
        path = f"/app/dom_diag_{date_str}_{prefix}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            log_info(f"DOM 진단 저장: {path}")
        except Exception as e:
            log_error(f"DOM 진단 파일 저장 실패: {e}")

        # save page source
        try:
            src = driver.page_source
            src_path = f"/app/dom_source_{date_str}_{prefix}.html"
            with open(src_path, "w", encoding="utf-8") as hf:
                hf.write(src)
            log_info(f"DOM source 저장: {src_path}")
        except Exception as e:
            log_error(f"DOM source 저장 실패: {e}")

        # save sample table HTML
        try:
            if out.get("table_count", 0) > 0:
                sample_html = tables[0].get_attribute('outerHTML')
            else:
                sample_html = ""
            sample_path = f"/app/dom_table_sample_{date_str}_{prefix}.html"
            with open(sample_path, "w", encoding="utf-8") as sf:
                sf.write(sample_html)
            log_info(f"DOM table sample 저장: {sample_path}")
        except Exception as e:
            log_error(f"DOM table sample 저장 실패: {e}")

        # save screenshot
        try:
            ss_path = f"/app/dom_screenshot_{date_str}_{prefix}.png"
            driver.save_screenshot(ss_path)
            log_info(f"DOM screenshot 저장: {ss_path}")
        except Exception as e:
            log_error(f"DOM screenshot 저장 실패: {e}")

        log_info(f"DOM diag {date_str} table_count={out['table_count']} rows={out['table_rows']} hash={out['table_text_hash']}")
    except Exception as e:
        log_error(f"log_dom_diagnostics 실패: {e}")

def fetch_sheet_version(date_str):
    try:
        r = requests.get(f"{API_BASE}/api/daily-sheet?date={date_str}", timeout=8)
        if r.ok:
            data = r.json()
            return data.get("version", 0)
    except Exception as e:
        log_error(f"{date_str} 버전 조회 실패: {e}")
    return 0


