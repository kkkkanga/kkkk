import os
import re
import json
import shutil
import logging
import traceback
import subprocess
import threading
import hashlib
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from fastapi.middleware.cors import CORSMiddleware
from models import DailySheetUpdatePayload
from fastapi import FastAPI, Request, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from db import SessionLocal
from sqlalchemy import select, func
from db_models import DailySheet, DailySheetRow, MemoQueue, MemoSyncFlag
from sqlalchemy import text
from fastapi import Body
from sqlalchemy import delete
from db import get_db
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

app = FastAPI()

KST = timezone(timedelta(hours=9))

logging.basicConfig(
    filename="fastapi_run_daypy.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(message)s"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1) .env 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
    
# ------------------ 경로 / 상수 ------------------
NAS_FOLDER = os.environ.get("NAS_FOLDER")  # ← .env의 NAS_FOLDER 우선
BAK_FOLDER = os.path.join(NAS_FOLDER, "bak")
PY_PATH = os.environ.get("DAYPY_PATH", "/app/day.py")
MEMO_SYNC_PATH = os.environ.get("MEMO_SYNC_PATH", os.path.join(os.getcwd(), "memo_sync.py"))
DAY_STATUS_FILE = "day_status.json"

# memo sync 관련
# 환경변수 MEMO_SYNC_PATH가 없으면 프로젝트 로컬 memo_sync.py 시도
MEMO_SYNC_STATE_FILE = "memo_sync_state.json"
MEMO_SYNC_DEBOUNCE_SECONDS = 10 # (초)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
proc = None
proc_lock = threading.Lock()

# memo sync globals
memo_sync_lock = threading.Lock()
memo_sync_timer: Optional[threading.Timer] = None
memo_sync_proc: Optional[subprocess.Popen] = None
last_memo_edit_ts: Optional[float] = None
scheduled_run_ts: Optional[float] = None
memo_sync_running = False

from routes_restore import router as restore_router


app.include_router(restore_router)

def _coalesce(val, default=None):
    return val if val is not None else default

def _row_from_json(date_str: str, row_json: dict) -> DailySheetRow:
    """
    JSON 한 행을 DailySheetRow 인스턴스로 변환.
    프로젝트의 실제 컬럼명에 맞춰 매핑하세요.
    """
    r = DailySheetRow()
    r.sheet_date = date_str
    # 예시 매핑(프로젝트 컬럼명에 맞게 조정 필요)
    r.site = _coalesce(row_json.get("사이트"))
    r.status = _coalesce(row_json.get("상태"))
    r.customer_name = _coalesce(row_json.get("고객명"))
    r.phone = _coalesce(row_json.get("연락처"))
    r.people = _coalesce(row_json.get("예약 인원"))
    r.car = _coalesce(row_json.get("차량"))
    r.reservation_date = _coalesce(row_json.get("예약일"))
    r.현장결제금액 = _coalesce(row_json.get("현장결제 금액"))
    r.선결제금액 = _coalesce(row_json.get("선결제 금액"))
    r.총이용료 = _coalesce(row_json.get("총 이용료"))
    r.관리메모 = _coalesce(row_json.get("관리메모"))
    r.요청사항 = _coalesce(row_json.get("요청사항"))
    r.circled = _coalesce(row_json.get("circled"))
    r.같이온사이트 = _coalesce(row_json.get("같이온사이트"))
    r.custom = _coalesce(row_json.get("__custom"))
    r.original = _coalesce(row_json.get("__original"))
    r.history = _coalesce(row_json.get("__history"))
    return r

@app.post("/api/restore-daily-sheet-from-json")
def restore_daily_sheet_from_json(
    date: str = Body(..., embed=True),
    path: str | None = Body(None, embed=True),
    force: bool = Body(True, embed=True),
    db: Session = Depends(get_db),
):
    """
    E:\date.json 같은 JSON을 읽어 해당 날짜 데이터를 DB에 강제 복원.
    - force=True: 기존 해당 날짜 데이터 전부 삭제 후 JSON 내용으로 재작성
    - path 미지정 시 NAS_FOLDER/date.json 사용
    """
    json_path = path or os.path.join(NAS_FOLDER, f"{date}.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"JSON not found: {json_path}")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"JSON load error: {e}")

    # 유효성
    if data.get("date") and data["date"] != date:
        # 파일의 date와 요청 date가 다른 경우 경고/거절
        raise HTTPException(status_code=400, detail=f"date mismatch: body={date}, file={data.get('date')}")

    top = data.get("top")
    headers = data.get("headers")
    stats = data.get("stats")
    footer = data.get("footer")
    optionCols = data.get("optionCols") or data.get("option_cols")
    sheet = data.get("sheet") or []

    # 트랜잭션
    try:
        # 1) 시트 메타 upsert
        ds: DailySheet | None = db.get(DailySheet, date)
        if not ds:
            ds = DailySheet(date=date, version=0)
            db.add(ds)

        # 2) force면 행 전부 삭제
        if force:
            db.execute(delete(DailySheetRow).where(DailySheetRow.sheet_date == date))

        # 3) 메타 갱신
        ds.version = (ds.version or 0) + 1
        # 안전하게 legacy 속성이나 신규 속성에 기록
        if hasattr(ds, "top_json"):
            ds.top_json = top
        else:
            ds.top = top
        if hasattr(ds, "headers_json"):
            ds.headers_json = headers
        else:
            ds.headers = headers
        if hasattr(ds, "stats_json"):
            ds.stats_json = stats
        else:
            ds.stats = stats
        if hasattr(ds, "footer_text"):
            ds.footer_text = footer
        else:
            ds.footer = footer
        if hasattr(ds, "option_cols_json"):
            ds.option_cols_json = optionCols
        else:
            ds.option_cols = optionCols

        # 4) 행 재작성
        inserted = 0
        for row_json in sheet:
            row_obj = _row_from_json(date, row_json)
            db.add(row_obj)
            inserted += 1

        db.commit()
        return {
            "ok": True,
            "date": date,
            "version": ds.version,
            "inserted_rows": inserted,
            "json_path": json_path,
            "force": force,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"restore failed: {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def utc_to_kst_str(dt):
    if not dt:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).isoformat()

# ======================================================================
# Memo Queue / Sync API (DB버전)
# ======================================================================
@app.get("/api/memo-queue")
def memo_queue_list(db: Session = Depends(get_db)):
    items = db.query(MemoQueue).order_by(MemoQueue.added_at.desc()).limit(200).all()
    def item_to_dict(item):
        return {
            "id": str(item.id),
            "site": item.site,
            "reservation_date": item.reservation_date,
            "customer_name": item.customer_name,
            "phone": item.phone,
            "memo": item.memo,
            "mode": item.mode,
            "status": item.status,
            "tries": item.tries,
            "added_at": item.added_at.isoformat() if item.added_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        }
    return {"queue": [item_to_dict(i) for i in items]}

@app.post("/api/memo-queue-append")
async def memo_queue_append(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except:
        raise HTTPException(status_code=400, detail="JSON 파싱 오류")
    site = body.get("site")
    reservation_date = body.get("reservation_date")
    customer_name = body.get("customer_name") or ""
    phone = body.get("phone") or ""
    memo_text = body.get("memo") or ""
    mode = body.get("mode") or "replace"
    if not site or not reservation_date:
        raise HTTPException(status_code=400, detail="site, reservation_date 필수")
    item = MemoQueue(
        id=uuid.uuid4(),
        site=site,
        reservation_date=reservation_date,
        customer_name=customer_name,
        phone=phone,
        memo=memo_text,
        mode=mode,
        status="pending",
        tries=0,
    )
    db.add(item)
    db.commit()
    return {"ok": True, "id": str(item.id)}

# ---- 메모 sync flag 갱신 + 디바운스 예약 ----
@app.post("/api/memo-edit-touch")
def memo_edit_touch(db: Session = Depends(get_db)):
    global last_memo_edit_ts
    now = datetime.utcnow()
    # DB 플래그 true
    flag = db.query(MemoSyncFlag).get(1)
    if not flag:
        flag = MemoSyncFlag(id=1, sync_required=True, requested_at=now)
        db.add(flag)
    else:
        flag.sync_required = True
        flag.requested_at = now
    db.commit()
    # 메모 편집 타임스탬프 저장 및 실행 예약
    with memo_sync_lock:
        last_memo_edit_ts = time.time()
        _schedule_memo_sync_locked()
        sched_at = utc_to_kst_str(datetime.utcfromtimestamp(scheduled_run_ts)) if scheduled_run_ts else None
    return {
        "ok": True,
        "last_edit_at": now.isoformat(),
        "scheduled_run_at": sched_at
    }

@app.get("/api/memo-sync-status")
def memo_sync_status():
    with memo_sync_lock:
        state = _load_memo_sync_state_file()
        state["running"] = memo_sync_running
        state["scheduled_run_at"] = utc_to_kst_str(datetime.utcfromtimestamp(scheduled_run_ts)) if scheduled_run_ts else None
        state["last_edit_at"] = utc_to_kst_str(datetime.utcfromtimestamp(last_memo_edit_ts)) if last_memo_edit_ts else state.get("last_edit_at")
        return state

@app.post("/api/memo-sync-run-now")
def memo_sync_run_now():
    with memo_sync_lock:
        if memo_sync_timer and memo_sync_timer.is_alive():
            memo_sync_timer.cancel()
    _run_memo_sync_subprocess()
    return {"ok": True, "forced": True}

# ------------------ memo sync state helpers ------------------
def _save_memo_sync_state(extra: Dict[str, Any] = None):
    state = {
        "last_edit_at": utc_to_kst_str(datetime.utcfromtimestamp(last_memo_edit_ts)) if last_memo_edit_ts else None,
        "scheduled_run_at": utc_to_kst_str(datetime.utcfromtimestamp(scheduled_run_ts)) if scheduled_run_ts else None,
        "running": memo_sync_running,
    }
    if extra:
        state.update(extra)
    try:
        with open(MEMO_SYNC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"memo_sync_state 저장 실패: {e}")

def _load_memo_sync_state_file() -> Dict[str, Any]:
    if not os.path.exists(MEMO_SYNC_STATE_FILE):
        return {}
    try:
        with open(MEMO_SYNC_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _run_memo_sync_subprocess():
    global memo_sync_proc, memo_sync_running, scheduled_run_ts
    with memo_sync_lock:
        now = time.time()
        if last_memo_edit_ts and now - last_memo_edit_ts < MEMO_SYNC_DEBOUNCE_SECONDS - 0.5:
            logging.info("[MEMO_SYNC] 최근 수정 감지 → 실행 연기")
            _schedule_memo_sync_locked()
            return
        if memo_sync_running:
            logging.info("[MEMO_SYNC] 이미 실행 중 → 중복 실행 건너뜀")
            return
        memo_sync_running = True
        scheduled_run_ts = None
        _save_memo_sync_state({"last_run_started_at": datetime.utcnow().isoformat()})

    def target():
        global memo_sync_proc, memo_sync_running
        logging.info(f"[MEMO_SYNC] memo_sync.py 실행 시작: {MEMO_SYNC_PATH}")
        stdout_text, stderr_text = "", ""
        rc = None
        try:
            workdir = os.path.dirname(MEMO_SYNC_PATH) or os.getcwd()
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            memo_sync_proc = subprocess.Popen(
                ['python', MEMO_SYNC_PATH, '--debug'],
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env
            )
            stdout_text, stderr_text = memo_sync_proc.communicate()
            rc = memo_sync_proc.returncode
            logging.info(f"[MEMO_SYNC] 종료 코드: {rc}")
            if stdout_text:
                logging.info(f"[memo_sync stdout]\n{stdout_text[:4000]}...")
            if stderr_text:
                logging.error(f"[memo_sync stderr]\n{stderr_text[:4000]}...")
        except Exception as e:
            logging.error(f"[MEMO_SYNC] 실행 오류: {e}\n{traceback.format_exc()}")
        finally:
            with memo_sync_lock:
                memo_sync_running = False
                _save_memo_sync_state({
                    "last_run_finished_at": datetime.utcnow().isoformat(),
                    "last_run_returncode": rc,
                })

    threading.Thread(target=target, daemon=True).start()

def _schedule_memo_sync_locked():
    global memo_sync_timer, scheduled_run_ts
    if memo_sync_timer and memo_sync_timer.is_alive():
        memo_sync_timer.cancel()
    scheduled_run_ts = time.time() + MEMO_SYNC_DEBOUNCE_SECONDS

    def timer_cb():
        _run_memo_sync_subprocess()

    memo_sync_timer = threading.Timer(MEMO_SYNC_DEBOUNCE_SECONDS, timer_cb)
    memo_sync_timer.daemon = True
    memo_sync_timer.start()
    _save_memo_sync_state()

# --------------------------------------------------------------------------------
# API: Daily Sheet
# --------------------------------------------------------------------------------
from sqlalchemy import text  # 이미 있다면 중복 추가 불필요

@app.get("/api/daily-sheet")
def get_daily_sheet(date: str = Query(...), db: Session = Depends(get_db)):
    try:
        sheet = db.get(DailySheet, date)
        if not sheet:
            raise HTTPException(status_code=404, detail=f"{date} 예약표 없음")

        # 표시용 사이트값(__custom->>'사이트')이 있으면 우선 사용, 없으면 원본 site로 정렬
        # Postgres JSONB 연산자 사용
        # Use DB column name 'custom_values' (created by migrations) for ordering
        order_sql = text(
            "COALESCE(NULLIF(trim((custom_values->>'사이트')::text), ''), site) ASC"
        )
        rows = (
            db.query(DailySheetRow)
              .filter(DailySheetRow.sheet_date == date)
              .order_by(order_sql, DailySheetRow.reservation_date.asc(), DailySheetRow.customer_name.asc())
              .all()
        )

        sheet_data = []
        for r in rows:
            sheet_data.append({
                "사이트": r.site,
                "상태": r.status,
                "고객명": r.customer_name,
                "연락처": r.phone,
                "예약 인원": r.people,
                "차량": r.car,
                "예약일": r.reservation_date,
                "현장결제 금액": r.현장결제금액,
                "선결제 금액": r.선결제금액,
                "총 이용료": r.총이용료,
                "관리메모": r.관리메모,
                "요청사항": r.요청사항,
                "circled": r.circled,
                "같이온사이트": r.같이온사이트,
                "__custom": r.custom,
                "__original": r.original,
                "__history": r.history
            })
        return {
            "date": sheet.date,
            "version": sheet.version,
            "updated_at": sheet.updated_at.isoformat() if sheet.updated_at else None,
            "top": sheet.top,
            "headers": sheet.headers,
            "stats": sheet.stats,
            "footer": sheet.footer,
            "optionCols": sheet.option_cols,
            "sheet_hash": sheet.sheet_hash,
            "e10": sheet.e10,
            "sheet": sheet_data
        }
    except HTTPException:
        # FastAPI HTTPException은 그대로 전달
        raise
    except Exception as e:
        # 상세한 스택트레이스를 로그에 남기고 500을 반환
        logging.error(f"get_daily_sheet error for date={date}: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="서버 내부 오류 (로그 확인 필요)")

@app.get("/api/daily-sheet/meta")
def get_daily_sheet_meta(date: str = Query(...), db: Session = Depends(get_db)):
    sheet = db.get(DailySheet, date)
    if not sheet:
        raise HTTPException(status_code=404, detail="파일 없음")
    return {
        "date": date,
        "version": sheet.version,
        "updated_at": sheet.updated_at.isoformat() if sheet.updated_at else None,
        "sheet_hash": sheet.sheet_hash
    }

@app.get("/api/available-dates")
def get_available_dates(db: Session = Depends(get_db)):
    dates = db.scalars(select(DailySheet.date)).all()
    dates_sorted = sorted(dates, reverse=True)
    return {"dates": dates_sorted}

def compute_sheet_hash(sheet_rows: List[Dict[str, Any]]) -> str:
    try:
        raw = json.dumps(sheet_rows, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except Exception:
        return ""

# ------------------ 예약표 저장 (전체) ------------------
@app.post("/api/update-daily-sheet")
async def update_daily_sheet(request: Request, db: Session = Depends(get_db)):
    try:
        raw_body = await request.json()
        e10 = raw_body.get("e10", True)  # 프론트에서 넘어오지 않으면 True
    except Exception:
        raise HTTPException(status_code=400, detail="JSON 파싱 오류")

    date = raw_body.get("date")
    if not date:
        raise HTTPException(status_code=400, detail="date 필드 필요")
    if "version" not in raw_body or raw_body.get("version") is None:
        raise HTTPException(status_code=428, detail="version(현재 파일 버전) 필요")

    try:
        payload = DailySheetUpdatePayload(**raw_body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"유효성 검증 실패: {e}")

    # Normalize date to actual date object to match DB DATE columns
    try:
        from datetime import date as _date
        if isinstance(date, str):
            date_obj = _date.fromisoformat(date)
        else:
            date_obj = date
    except Exception:
        raise HTTPException(status_code=400, detail="date 형식 오류, YYYY-MM-DD 필요")

    # Upsert sheet metadata
    sheet = db.get(DailySheet, date_obj)
    if sheet:
        if sheet.version != payload.version:
            return JSONResponse({"error": "버전 불일치 (다른 사용자가 먼저 저장)", "current_version": sheet.version}, status_code=409)
        # 업데이트 경로
        sheet.version += 1
        sheet.updated_at = datetime.utcnow()
        sheet.top = payload.top
        sheet.headers = payload.headers
        sheet.stats = payload.stats
        sheet.option_cols = payload.optionCols
        # 기존 row 삭제 후 재삽입
        db.query(DailySheetRow).filter(DailySheetRow.sheet_date == date_obj).delete()
        sheet.e10 = e10
    else:
        # 신규 생성 경로
        sheet = DailySheet(
            date=date_obj,
            version=1,
            updated_at=datetime.utcnow(),
            top=payload.top,
            headers=payload.headers,
            stats=payload.stats,
            option_cols=payload.optionCols,
            e10=e10
        )
        db.add(sheet)
        db.flush()

    sheet_serializable = []
    for row_model in payload.sheet:
        row_dict = row_model.model_dump(by_alias=True)
        sheet_serializable.append(row_dict)

        # convert reservation_date string to date where applicable
        resv = row_dict.get("예약일")
        try:
            from datetime import date as _date
            if isinstance(resv, str) and resv:
                resv_val = _date.fromisoformat(resv)
            else:
                resv_val = resv
        except Exception:
            resv_val = row_dict.get("예약일")

    # Per-row debug info removed (was used during type troubleshooting)

        # Normalize list/array-like fields so they match DB column types (text[])
        def _normalize_list_field(v):
            # None -> empty list
            if v is None:
                return []
            # already a list
            if isinstance(v, list):
                return [str(x) for x in v]
            # JSON encoded string like '["a","b"]'
            if isinstance(v, str):
                s = v.strip()
                if s == "" or s.lower() == "null":
                    return []
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except Exception:
                    # fallback: split on newlines
                    return [part.strip() for part in s.split("\n") if part.strip()]
            # fallback: single value -> list
            return [str(v)]

        manage_memo_norm = _normalize_list_field(row_dict.get("관리메모"))
        together_sites_norm = _normalize_list_field(row_dict.get("같이온사이트"))
    # Normalized list fields (manage_memo / together_sites) will be passed as Python lists

        # Ensure we explicitly pass a datetime.date for sheet_date
        row_obj = DailySheetRow(
            sheet_date=date_obj,
            site=row_dict.get("사이트", ""),
            status=row_dict.get("상태"),
            customer_name=row_dict.get("고객명"),
            phone=row_dict.get("연락처"),
            people=row_dict.get("예약 인원"),
            car=row_dict.get("차량"),
            reservation_date=resv_val,
            현장결제금액=row_dict.get("현장결제 금액"),
            선결제금액=row_dict.get("선결제 금액"),
            총이용료=row_dict.get("총 이용료"),
            관리메모=manage_memo_norm,
            요청사항=row_dict.get("요청사항"),
            circled=row_dict.get("circled"),
            같이온사이트=together_sites_norm,
            custom=row_dict.get("__custom"),
            original=row_dict.get("__original"),
            history=row_dict.get("__history"),
        )
        db.add(row_obj)

    sheet.sheet_hash = compute_sheet_hash(sheet_serializable)
    db.commit()
    return {"ok": True, "version": sheet.version, "sheet_hash": sheet.sheet_hash}

# ------------------ 예약표 partial PATCH ------------------
@app.patch("/api/daily-sheet/row")
async def patch_single_row(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    date = body.get("date")
    version = body.get("version")
    key = body.get("key") or {}
    update_data = body.get("update") or {}
    if not date or version is None:
        raise HTTPException(status_code=400, detail="date, version 필수")
    key_site = key.get("사이트")
    key_resvdate = key.get("예약일")
    if not key_site or not key_resvdate:
        raise HTTPException(status_code=400, detail="key.사이트 / key.예약일 필요")

    sheet = db.get(DailySheet, date)
    if not sheet:
        raise HTTPException(status_code=404, detail="파일 없음")
    if version != sheet.version:
        return JSONResponse({"error": "버전 불일치", "current_version": sheet.version}, status_code=409)

    row = db.query(DailySheetRow).filter(
        DailySheetRow.sheet_date == date,
        DailySheetRow.site == key_site,
        DailySheetRow.reservation_date == key_resvdate
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="행을 찾을 수 없음")

    changed_cols = []
    # 관리메모: 항상 리스트로 표준화
    if "관리메모" in update_data:
        # E10 사이트는 메모 큐 적재/싱크 제외
        if row.site == "E10":
            new_val = update_data["관리메모"]
            if isinstance(new_val, list):
                std_memos = [str(v).strip() for v in new_val if str(v).strip()]
            elif new_val is None:
                std_memos = []
            else:
                txt = str(new_val).strip()
                std_memos = [s.strip() for s in txt.split("\n") if s.strip()] if "\n" in txt else ([txt] if txt else [])
            if row.관리메모 != std_memos:
                row.관리메모 = std_memos
                changed_cols.append("관리메모")
            # 메모 큐 적재는 하지 않음
        else:
            new_val = update_data["관리메모"]
            if isinstance(new_val, list):
                std_memos = [str(v).strip() for v in new_val if str(v).strip()]
            elif new_val is None:
                std_memos = []
            else:
                txt = str(new_val).strip()
                std_memos = [s.strip() for s in txt.split("\n") if s.strip()] if "\n" in txt else ([txt] if txt else [])
            if row.관리메모 != std_memos:
                row.관리메모 = std_memos
                changed_cols.append("관리메모")
            # 메모 큐에 적재(이전 버전 동작 유지)
            db.add(MemoQueue(
                id=uuid.uuid4(),
                site=row.site,
                reservation_date=row.reservation_date,
                customer_name=row.customer_name,
                phone=row.phone,
                memo="\n".join(std_memos),
                mode="replace",
                status="pending",
                tries=0,
            ))

    if changed_cols:
        sheet.version += 1
        sheet.updated_at = datetime.utcnow()
        # sheet_hash 다시 계산
        rows = db.query(DailySheetRow).filter(DailySheetRow.sheet_date == date).all()
        row_dicts = []
        for r in rows:
            row_dicts.append({
                "사이트": r.site,
                "상태": r.status,
                "고객명": r.customer_name,
                "연락처": r.phone,
                "예약 인원": r.people,
                "차량": r.car,
                "예약일": r.reservation_date,
                "현장결제 금액": r.현장결제금액,
                "선결제 금액": r.선결제금액,
                "총 이용료": r.총이용료,
                "관리메모": r.관리메모,
                "요청사항": r.요청사항,
                "circled": r.circled,
                "같이온사이트": r.같이온사이트,
                "__custom": r.custom,
                "__original": r.original,
                "__history": r.history,
            })
        sheet.sheet_hash = compute_sheet_hash(row_dicts)
        db.commit()
        updated_row = {
            "사이트": row.site,
            "상태": row.status,
            "고객명": row.customer_name,
            "연락처": row.phone,
            "예약 인원": row.people,
            "차량": row.car,
            "예약일": row.reservation_date,
            "현장결제 금액": row.현장결제금액,
            "선결제 금액": row.선결제금액,
            "총 이용료": row.총이용료,
            "관리메모": row.관리메모,
            "요청사항": row.요청사항,
            "circled": row.circled,
            "같이온사이트": row.같이온사이트,
            "__custom": row.custom,
            "__original": row.original,
            "__history": row.history
        }
        # 메모 편집 신호를 받는 프런트에 예약 정보 제공을 위해 memo-edit-touch는 클라이언트에서 호출
        return {
            "ok": True,
            "version": sheet.version,
            "updated_row": updated_row,
            "changed_cols": changed_cols,
            "sheet_hash": sheet.sheet_hash
        }

    db.commit()
    return {"ok": True, "version": sheet.version, "changed_cols": []}

# --------------------------- day.py 실행 ---------------------------
@app.post("/api/run-day-py")
async def run_day_py(request: Request):
    global proc
    logging.info("/api/run-day-py called")
    with proc_lock:
        if proc and proc.poll() is None:
            return JSONResponse(content={"status": "이미 실행 중"}, status_code=409)

        def target():
            global proc
            logging.info(f"day.py 실행 시작: {PY_PATH}")
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                proc = subprocess.Popen(
                    ['python', PY_PATH],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    env=env
                )
                out, err = proc.communicate()
                logging.info(f"day.py 종료 코드: {proc.returncode}")
                if out:
                    logging.info(f"[day.py stdout]\n{out}")
                if err:
                    logging.error(f"[day.py stderr]\n{err}")
            except Exception as e:
                logging.error(f"day.py 실행 중 에러: {e}")

        threading.Thread(target=target, daemon=True).start()

    return {"status": "실행 시작"}

@app.get("/api/run-day-py/status")
def run_day_py_status():
    if os.path.exists(DAY_STATUS_FILE):
        try:
            with open(DAY_STATUS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"상태 파일 읽기 실패: {e}")
            raise HTTPException(status_code=500, detail="상태 파일 읽기 실패")
    return {"status": "idle"}