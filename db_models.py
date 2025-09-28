from sqlalchemy import (
    Column, Integer, Text, DateTime, ForeignKey,
    UniqueConstraint, Boolean, Date
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ARRAY
from sqlalchemy.sql import func
import uuid
from db import Base

class DailySheet(Base):
    __tablename__ = "daily_sheets"
    # DB stores date as SQL DATE
    date = Column(Date, primary_key=True)          # 'YYYY-MM-DD' as date
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    top = Column(JSONB, nullable=False)
    headers = Column(JSONB, nullable=False)
    stats = Column(JSONB, nullable=False, default=dict)
    footer = Column(Text, nullable=True)
    option_cols = Column(JSONB, nullable=True)
    sheet_hash = Column(Text, nullable=True)
    e10 = Column(Boolean, nullable=True)

    # Backwards-compatible aliases for legacy attribute names used elsewhere in the codebase.
    # These map older names (e.g. top_json) to the new columns (top, headers, stats, footer, option_cols).
    @property
    def top_json(self):
        return self.top

    @top_json.setter
    def top_json(self, v):
        self.top = v

    @property
    def headers_json(self):
        return self.headers

    @headers_json.setter
    def headers_json(self, v):
        self.headers = v

    @property
    def stats_json(self):
        return self.stats

    @stats_json.setter
    def stats_json(self, v):
        self.stats = v

    @property
    def footer_text(self):
        return self.footer

    @footer_text.setter
    def footer_text(self, v):
        self.footer = v

    @property
    def option_cols_json(self):
        return self.option_cols

    @option_cols_json.setter
    def option_cols_json(self, v):
        self.option_cols = v

class DailySheetRow(Base):
    # NOTE: DB currently has table named 'daily_sheet_rows' (created by migrations),
    # so keep model tablename in sync to avoid "relation does not exist" errors.
    __tablename__ = "daily_sheet_rows"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # sheet_date is a SQL DATE column in DB
    sheet_date = Column("sheet_date", Date, ForeignKey("daily_sheets.date", ondelete="CASCADE"), nullable=False)
    site = Column("site", Text, nullable=False)
    status = Column("status", Text, nullable=True)
    customer_name = Column("customer_name", Text, nullable=True)
    phone = Column("phone", Text, nullable=True)
    # DB uses different column names (raw suffixes / english names)
    people = Column("people_raw", Text, nullable=True)                 # 예약 인원
    car = Column("car_raw", Text, nullable=True)                        # 차량
    reservation_date = Column("reservation_date", Text, nullable=True)  # '9/7 ~ 9/9'
    현장결제금액 = Column("onsite_amount", Text, nullable=True)
    선결제금액 = Column("prepaid_amount", Text, nullable=True)
    총이용료 = Column("total_amount", Text, nullable=True)
    # DB has manage_memo as text[] (array of text) — use ARRAY(Text) to match
    관리메모 = Column("manage_memo", ARRAY(Text), nullable=True)              # 리스트 형태 (text[])
    요청사항 = Column("request_note", Text, nullable=True)
    circled = Column("circled", JSONB, nullable=True)                  # dict
    # DB stores together_sites as text[] (array)
    같이온사이트 = Column("together_sites", ARRAY(Text), nullable=True)      # list (text[])
    custom = Column("custom_values", JSONB, nullable=True)
    original = Column("original_values", JSONB, nullable=True)
    history = Column("history", JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("sheet_date", "site", "reservation_date", name="uix_sheet_site_resvdate"),
    )

class MemoQueue(Base):
    __tablename__ = "memo_queue"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site = Column(Text, nullable=False)
    reservation_date = Column(Text, nullable=False)  # ← 여기 반드시 Text!
    customer_name = Column(Text, nullable=False)
    phone = Column(Text, nullable=False)
    memo = Column(Text, nullable=False)
    mode = Column(Text, nullable=False)          # 'append' or 'replace'
    status = Column(Text, nullable=False, default="pending")
    tries = Column(Integer, nullable=False, default=0)
    added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

class MemoSyncFlag(Base):
    __tablename__ = "memo_sync_flag"
    id = Column(Integer, primary_key=True, default=1)
    sync_required = Column(Boolean, nullable=False, default=False)
    requested_at = Column(DateTime(timezone=True), nullable=True)