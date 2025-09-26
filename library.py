"""
도서 대출 프로그램 (YES24 엑셀 데이터 기반)

특징:
  - YES24 베스트셀러 데이터를 초기 도서목록으로 사용
  - 도서 데이터: yes24_bestsellers.xlsx 파일에서 자동 로드
  - 회원/대출 데이터: JSON 파일로 관리
  - 대화식 CLI 인터페이스 제공
  - 자가 테스트 모드 지원

기능:
  - 기본 도서 관리: 도서 등록/조회/검색, 복본(실물 책) 개별 식별
  - 회원 관리: 등록/조회(학번, 이름, 연락처), 회원/관리자 로그인
  - 대출/반납: 대출 등록, 반납 처리, 14일 기한 안내(텍스트 출력)
  - 관리자 기능: 도서 신규 등록/삭제(논리), 대출 현황(전체/미반납)
  - 날짜: 실행 시 가상의 오늘(YYYY-MM-DD). 과거로 시간여행 금지

실행 방법:
  - 대화식 모드: `python library.py`
  - 자가 테스트: `python library.py --mode selftest`
  
사전 준비:
  - YES24 크롤러 실행: `python yes24_crawler.py` (yes24_bestsellers.xlsx 생성)
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import pandas as pd
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import List, Optional

# -------------------- 경로/설정 --------------------
DEFAULT_DATA_DIR = "data"
EXCEL_FILE = "yes24_bestsellers.xlsx"
DUE_DAYS = 14

# -------------------- 유틸 --------------------

def ensure_data_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # 수동 편집 방지: 파싱 에러 시 빈 구조로 초기화
            return default


def _write_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def norm_author_key(author: str) -> str:
    return " ".join(author.strip().lower().split())


def parse_date(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def date_str(dt: date) -> str:
    return dt.isoformat()

# -------------------- 데이터 클래스 --------------------

@dataclass
class Work:
    work_id: int
    title: str
    author_key: str
    author_display: str
    registered_date: str
    deleted_date: Optional[str] = None

@dataclass
class Copy:
    copy_id: int
    work_id: int
    status: str  # "available" | "loaned" | "deleted"
    registered_date: str
    deleted_date: Optional[str] = None

@dataclass
class Member:
    student_id: str
    name: str
    phone: str
    password: str
    registered_date: str

@dataclass
class Loan:
    loan_id: int
    copy_id: int
    work_id: int
    student_id: str
    loan_date: str
    due_date: str
    return_date: Optional[str] = None

# -------------------- 저장소 --------------------

class Repository:
    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        self.data_dir = data_dir
        ensure_data_dir(self.data_dir)
        self._members_file = os.path.join(self.data_dir, "members.json")
        self._loans_file = os.path.join(self.data_dir, "loans.json")
        self._works_file = os.path.join(self.data_dir, "works.json")
        self._copies_file = os.path.join(self.data_dir, "copies.json")
        self._deleted_works_file = os.path.join(self.data_dir, "deleted_works.json")
        
        # JSON 파일에서 데이터 로드
        self.works: List[Work] = self._load_works_from_json()
        self.copies: List[Copy] = self._load_copies_from_json()
        self.deleted_works: List[Work] = self._load_deleted_works_from_json()
        
        # 회원과 대출 정보는 JSON으로 관리 (기존 데이터 호환성 고려)
        members_data = _read_json(self._members_file, [])
        self.members: List[Member] = []
        for m in members_data:
            # 빈 객체나 필수 필드가 없는 데이터는 건너뛰기
            if not m or 'student_id' not in m or not m.get('student_id'):
                continue
            
            # 기존 데이터에 password가 없는 경우 기본값 설정
            if 'password' not in m:
                m['password'] = 'password123'  # 기본 비밀번호
            
            # username 필드가 있으면 제거 (호환성을 위해)
            if 'username' in m:
                del m['username']
            
            # 필수 필드들이 모두 있는지 확인
            required_fields = ['student_id', 'name', 'phone', 'registered_date']
            if all(field in m and m[field] for field in required_fields):
                self.members.append(Member(**m))
        
        self.loans: List[Loan] = [Loan(**l) for l in _read_json(self._loans_file, [])]

    def _load_works_from_json(self) -> List[Work]:
        """JSON 파일에서 도서 데이터를 로드합니다."""
        works_data = _read_json(self._works_file, [])
        if not works_data:
            # JSON 파일이 없으면 엑셀에서 초기 데이터 생성
            return self._initialize_from_excel()
        return [Work(**w) for w in works_data]

    def _load_copies_from_json(self) -> List[Copy]:
        """JSON 파일에서 복본 데이터를 로드합니다."""
        copies_data = _read_json(self._copies_file, [])
        if not copies_data:
            return []
        return [Copy(**c) for c in copies_data]

    def _load_deleted_works_from_json(self) -> List[Work]:
        """JSON 파일에서 삭제된 도서 데이터를 로드합니다."""
        deleted_data = _read_json(self._deleted_works_file, [])
        return [Work(**w) for w in deleted_data]


    def _initialize_from_excel(self) -> List[Work]:
        """엑셀 파일에서 초기 데이터를 생성하고 JSON으로 저장합니다."""
        works = self._load_works_from_excel()
        if works:
            # JSON 파일로 저장
            _write_json(self._works_file, [asdict(w) for w in works])
            # 복본도 생성하여 저장
            copies = self._generate_copies_from_works(works)
            _write_json(self._copies_file, [asdict(c) for c in copies])
            print("엑셀 파일에서 초기 데이터를 JSON으로 변환했습니다.")
        else:
            # 엑셀 파일이 없으면 빈 JSON 파일 생성
            _write_json(self._works_file, [])
            _write_json(self._copies_file, [])
            print("엑셀 파일이 없어 빈 도서 목록으로 시작합니다.")
        return works

    def _load_works_from_excel(self) -> List[Work]:
        """엑셀 파일에서 도서 데이터를 로드합니다."""
        try:
            if not os.path.exists(EXCEL_FILE):
                print(f"엑셀 파일을 찾을 수 없습니다: {EXCEL_FILE}")
                print("YES24 크롤러를 먼저 실행해주세요: python yes24_crawler.py")
                return []
            
            self.excel_df = pd.read_excel(EXCEL_FILE, engine='openpyxl')  # 복본 생성에 사용하기 위해 저장
            works = []
            
            for index, row in self.excel_df.iterrows():
                work = Work(
                    work_id=index + 1,  # 1부터 시작하는 ID
                    title=str(row['제목']),
                    author_key=norm_author_key(str(row['저자'])),
                    author_display=str(row['저자']),
                    registered_date=str(row['등록일']),
                    deleted_date=None
                )
                works.append(work)
            
            print(f"엑셀 파일에서 {len(works)}개의 도서를 로드했습니다.")
            return works
            
        except Exception as e:
            print(f"엑셀 파일 로드 중 오류: {e}")
            print("YES24 크롤러를 먼저 실행해주세요: python yes24_crawler.py")
            return []

    def _generate_copies_from_works(self, works: List[Work] = None) -> List[Copy]:
        """도서 목록으로부터 복본을 생성합니다. 엑셀 파일의 책개수 정보를 반영합니다."""
        if works is None:
            works = self.works
        copies = []
        copy_id = 1
        
        for work in works:
            # 엑셀 파일에서 해당 도서의 책개수 정보 가져오기
            copies_count = 1  # 기본값
            if hasattr(self, 'excel_df'):
                try:
                    # work_id는 1부터 시작하므로 index는 work_id - 1
                    row_index = work.work_id - 1
                    if row_index < len(self.excel_df):
                        copies_count = int(self.excel_df.iloc[row_index]['책개수'])
                except:
                    copies_count = 1  # 오류 시 기본값
            
            # 지정된 개수만큼 복본 생성
            for _ in range(copies_count):
                copy = Copy(
                    copy_id=copy_id,
                    work_id=work.work_id,
                    status="available",
                    registered_date=work.registered_date,
                    deleted_date=None
                )
                copies.append(copy)
                copy_id += 1
        
        print(f"총 {len(copies)}개의 복본을 생성했습니다.")
        return copies

    def persist(self):
        """모든 데이터를 JSON 파일로 저장합니다."""
        _write_json(self._members_file, [asdict(m) for m in self.members])
        _write_json(self._loans_file, [asdict(l) for l in self.loans])
        _write_json(self._works_file, [asdict(w) for w in self.works])
        _write_json(self._copies_file, [asdict(c) for c in self.copies])
        _write_json(self._deleted_works_file, [asdict(w) for w in self.deleted_works])

# -------------------- 서비스 로직 --------------------

class LibraryService:
    def __init__(self, repo: Repository, today: date):
        self.repo = repo
        self.today = today
        # ID 생성기: 현재 최대값+1 (고유성 보장)
        self._next_work_id = self._get_next_unique_id([w.work_id for w in repo.works])
        self._next_copy_id = self._get_next_unique_id([c.copy_id for c in repo.copies])
        self._next_loan_id = self._get_next_unique_id([l.loan_id for l in repo.loans])
        
        # 데이터 정합성 검사 및 수정
        self._validate_and_fix_data_integrity()

    # ---- 데이터 무결성 검사 ----
    def _get_next_unique_id(self, existing_ids):
        """고유한 ID를 생성합니다."""
        if not existing_ids:
            return 1
        next_id = max(existing_ids) + 1
        # 중복이 있다면 고유한 ID를 찾을 때까지 증가
        while next_id in existing_ids:
            next_id += 1
        return next_id

    def _validate_and_fix_data_integrity(self):
        """데이터 정합성을 검사하고 수정합니다."""
        print("데이터 정합성 검사 중...")
        
        # 1. 삭제된 work를 참조하는 copy 정리
        self._fix_deleted_work_references()
        
        # 2. 무효한 참조 제거
        self._remove_invalid_references()
        
        # 3. 중복 대출 방지
        self._fix_duplicate_loans()
        
        # 4. 날짜 논리 검사
        self._fix_date_logic()
        
        print("데이터 정합성 검사 완료")

    def _fix_deleted_work_references(self):
        """삭제된 work를 참조하는 copy를 정리합니다."""
        deleted_work_ids = {w.work_id for w in self.repo.deleted_works}
        fixed_count = 0
        
        for copy in self.repo.copies:
            if copy.work_id in deleted_work_ids and copy.deleted_date is None:
                copy.deleted_date = date_str(self.today)
                if copy.status == "available":
                    copy.status = "deleted"
                fixed_count += 1
        
        if fixed_count > 0:
            print(f"삭제된 work를 참조하는 {fixed_count}개의 복본을 정리했습니다.")

    def _remove_invalid_references(self):
        """무효한 참조를 제거합니다."""
        valid_work_ids = {w.work_id for w in self.repo.works}
        valid_student_ids = {m.student_id for m in self.repo.members}
        valid_copy_ids = {c.copy_id for c in self.repo.copies}
        
        # 무효한 work_id를 참조하는 copy 제거
        invalid_copies = [c for c in self.repo.copies if c.work_id not in valid_work_ids]
        for copy in invalid_copies:
            self.repo.copies.remove(copy)
        
        # 무효한 student_id를 참조하는 loan 제거
        invalid_loans = [l for l in self.repo.loans if l.student_id not in valid_student_ids]
        for loan in invalid_loans:
            self.repo.loans.remove(loan)
        
        # 무효한 copy_id를 참조하는 loan 제거
        invalid_loans = [l for l in self.repo.loans if l.copy_id not in valid_copy_ids]
        for loan in invalid_loans:
            self.repo.loans.remove(loan)
        
        if invalid_copies or invalid_loans:
            print(f"무효한 참조 {len(invalid_copies + invalid_loans)}개를 제거했습니다.")

    def _fix_duplicate_loans(self):
        """중복 대출을 수정합니다."""
        # 동일한 copy_id에 대해 return_date=null인 대출이 2개 이상인 경우
        active_loans_by_copy = {}
        for loan in self.repo.loans:
            if loan.return_date is None:
                if loan.copy_id not in active_loans_by_copy:
                    active_loans_by_copy[loan.copy_id] = []
                active_loans_by_copy[loan.copy_id].append(loan)
        
        fixed_count = 0
        for copy_id, loans in active_loans_by_copy.items():
            if len(loans) > 1:
                # 가장 최근 대출만 남기고 나머지는 반납 처리
                loans.sort(key=lambda x: x.loan_date, reverse=True)
                for loan in loans[1:]:  # 첫 번째(가장 최근) 제외
                    loan.return_date = loan.loan_date  # 대출일과 같은 날 반납 처리
                    fixed_count += 1
        
        if fixed_count > 0:
            print(f"중복 대출 {fixed_count}개를 수정했습니다.")

    def _fix_date_logic(self):
        """날짜 논리 오류를 수정합니다."""
        fixed_count = 0
        
        for loan in self.repo.loans:
            # due_date = loan_date + 14일 검증
            expected_due_date = parse_date(loan.loan_date) + timedelta(days=DUE_DAYS)
            actual_due_date = parse_date(loan.due_date)
            
            if actual_due_date != expected_due_date:
                loan.due_date = date_str(expected_due_date)
                fixed_count += 1
            
            # return_date가 있으면 loan_date ≤ return_date 검증
            if loan.return_date is not None:
                loan_date = parse_date(loan.loan_date)
                return_date = parse_date(loan.return_date)
                if return_date < loan_date:
                    loan.return_date = loan.loan_date  # 대출일과 같은 날로 수정
                    fixed_count += 1
        
        if fixed_count > 0:
            print(f"날짜 논리 오류 {fixed_count}개를 수정했습니다.")

    def _validate_fk(self, work_id: int = None, student_id: str = None, copy_id: int = None):
        """참조 무결성을 검사합니다."""
        if work_id is not None:
            if not any(w.work_id == work_id for w in self.repo.works):
                raise ValueError(f"존재하지 않는 work_id: {work_id}")
        
        if student_id is not None:
            if not any(m.student_id == student_id for m in self.repo.members):
                raise ValueError(f"존재하지 않는 student_id: {student_id}")
        
        if copy_id is not None:
            if not any(c.copy_id == copy_id for c in self.repo.copies):
                raise ValueError(f"존재하지 않는 copy_id: {copy_id}")
     

     #학번 규칙, 이름 규칙, 연락처 규칙, 비밀번호 규칙 추가
    def _validate_student_id(self, student_id: str) -> bool:
        """학번 검증: 1931년부터 2025년까지의 9자리 숫자"""
        import re
        pattern = r'^(193[1-9]|19[4-9][0-9]|20[01][0-9]|202[0-5])[0-9]{5}$'
        if not re.match(pattern, student_id):
            print("학번 형식이 올바르지 않습니다. (1931년~2025년, 9자리 숫자)")
            return False
        return True

    def _validate_name(self, name: str) -> bool:
        """이름 검증: 2~4자 한글만"""

        import re

        # 길이 검사
        if len(name) < 2 or len(name) > 4:
            print("이름은 2~4자여야 합니다.")
            return False
        
        # 한글 검사
        if not re.match(r'^[가-힣]+$', name):
           
             # 영어나 숫자, 특수문자가 포함된 경우
            if re.search(r'[a-zA-Z0-9]', name):
                print("이름은 한글로 된 2~4자리여야 합니다.")
            else:
                print("이름은 한글로 된 2~4자리여야 합니다.")
            return False
        
        return True

    def _validate_phone(self, phone: str) -> bool:
        """연락처 검증: 010-XXXX-XXXX 또는 01X-XXX-XXXX 형식"""
        import re
        pattern = r'^01(0-[0-9]{4}|[1-9]-[0-9]{3,4})-[0-9]{4}$'
        if not re.match(pattern, phone):
            print("연락처 형식이 올바르지 않습니다. (010-XXXX-XXXX 또는 01X-XXX-XXXX)")
            return False
        return True

    def _validate_password(self, password: str) -> bool:
        """비밀번호 검증: 4~20자, 공백 없음"""
        if len(password) < 4 or len(password) > 20:
            print("비밀번호는 4~20자여야 합니다.")
            return False
        if ' ' in password or '\t' in password or '\n' in password:
            print("비밀번호에 공백이 포함될 수 없습니다.")
            return False
        return True
        


    # ---- 날짜 제어 ----
    def set_today(self, new_date: date):
        if new_date < self.today:
            print("과거로 이동은 허용되지 않습니다.")
            return
        self.today = new_date
        print(f"가상의 오늘 날짜가 {self.today} 로 설정되었습니다.")

    # ---- 도서/복본 관리 ----
    def add_work(self, title: str, author_display: str, copies: int = 1):
        # 입력값 검증
        if not title or not title.strip():
            print("제목을 입력해주세요.")
            return
        if not author_display or not author_display.strip():
            print("저자를 입력해주세요.")
            return
        if copies < 1:
            print("복본 수는 1 이상이어야 합니다.")
            return
        
        author_key = norm_author_key(author_display)
        # 동일 도서(제목+저자키) 존재 여부 확인
        existing = [w for w in self.repo.works if w.title == title.strip() and w.author_key == author_key and w.deleted_date is None]
        if existing:
            work = existing[0]
            print(f"기존 도서에 복본 {copies}권 추가: work_id={work.work_id}")
        else:
            work = Work(
                work_id=self._next_work_id,
                title=title.strip(),
                author_key=author_key,
                author_display=author_display.strip(),
                registered_date=date_str(self.today),
                deleted_date=None,
            )
            self.repo.works.append(work)
            self._next_work_id += 1
            print(f"도서 등록 완료: work_id={work.work_id}")
        
        # 복본 생성 (FK 검사: work_id가 존재하는지 확인)
        try:
            self._validate_fk(work_id=work.work_id)
        except ValueError as e:
            print(f"오류: {e}")
            # work를 다시 제거
            if work in self.repo.works:
                self.repo.works.remove(work)
            return
        
        for _ in range(max(1, int(copies))):
            cp = Copy(
                copy_id=self._next_copy_id,
                work_id=work.work_id,
                status="available",
                registered_date=date_str(self.today),
                deleted_date=None,
            )
            self.repo.copies.append(cp)
            self._next_copy_id += 1
        self.repo.persist()

    def delete_work(self, work_id: int):
        work = self._find_work(work_id)
        if not work or work.deleted_date is not None:
            print("도서가 존재하지 않거나 이미 삭제되었습니다.")
            return

      # 대출 중인 도서가 있는지 확인
        active_loans = [l for l in self.repo.loans if l.work_id == work_id and l.return_date is None]
        if active_loans:
            print(f"대출 중인 도서가 {len(active_loans)}건 있어 삭제할 수 없습니다.")
            print("모든 대출이 반납된 후 삭제해주세요.")
            return
        
        # 삭제된 도서를 deleted_works에 추가
        work.deleted_date = date_str(self.today)
        self.repo.deleted_works.append(work)
        
        # 연결된 복본도 논리삭제
        for c in self.repo.copies:
            if c.work_id == work_id and c.deleted_date is None:
                c.deleted_date = date_str(self.today)
                if c.status == "available":
                    c.status = "deleted"
        
        self.repo.persist()
        print(f"도서(work_id={work_id}) 및 복본 논리삭제 완료")

    def list_works(self):
        rows = []
        # 삭제된 도서 ID 목록 생성
        deleted_work_ids = {w.work_id for w in self.repo.deleted_works}
        
        for w in self.repo.works:
            if w.work_id not in deleted_work_ids:
                copies_total = sum(1 for c in self.repo.copies if c.work_id == w.work_id and c.work_id not in deleted_work_ids)
                copies_avail = sum(1 for c in self.repo.copies if c.work_id == w.work_id and c.work_id not in deleted_work_ids and c.status == "available")
                rows.append((w.work_id, w.title, w.author_display, copies_avail, copies_total))
        if not rows:
            print("등록된 도서가 없습니다.")
            return
        print("\n[도서 목록]")
        print("work_id | 제목 | 저자 | 대출가능/총복본")
        print("-" * 100)
        for r in rows:
            print(f"  {r[0]:>3} | {r[1]} | {r[2]} | {r[3]}/{r[4]}")

    def search_works(self, keyword: str):
        key = keyword.strip().lower()
        results = []
        # 삭제된 도서 ID 목록 생성
        deleted_work_ids = {w.work_id for w in self.repo.deleted_works}
        
        for w in self.repo.works:
            if w.work_id not in deleted_work_ids:
                if key in w.title.lower() or key in w.author_display.lower():
                    results.append(w)
        if not results:
            print("검색 결과가 없습니다.")
            return
        print("\n[검색 결과]")
        print("work_id | 제목 | 저자 | 대출가능/총복본")
        print("-" * 100)
        for w in results:
            copies_total = sum(1 for c in self.repo.copies if c.work_id == w.work_id and c.work_id not in deleted_work_ids)
            copies_avail = sum(1 for c in self.repo.copies if c.work_id == w.work_id and c.work_id not in deleted_work_ids and c.status == "available")
            print(f"  {w.work_id:>3} | {w.title} | {w.author_display} | {copies_avail}/{copies_total}")

    def _find_work(self, work_id: int) -> Optional[Work]:
        # 삭제된 도서 ID 목록 생성
        deleted_work_ids = {w.work_id for w in self.repo.deleted_works}
        
        for w in self.repo.works:
            if w.work_id == work_id and w.work_id not in deleted_work_ids:
                return w
        return None

    def _find_available_copy(self, work_id: int) -> Optional[Copy]:
        # 삭제된 도서 ID 목록 생성
        deleted_work_ids = {w.work_id for w in self.repo.deleted_works}
        
        for c in self.repo.copies:
            if c.work_id == work_id and c.work_id not in deleted_work_ids and c.deleted_date is None and c.status == "available":
                return c
        return None

    # ---- 회원 ----
    def register_member(self, student_id: str, name: str, phone: str, password: str):
        # 입력값 검증
        if not student_id or not student_id.strip():
            print("학번을 입력해주세요.")
            return
        if not name or not name.strip():
            print("이름을 입력해주세요.")
            return
        if not phone or not phone.strip():
            print("연락처를 입력해주세요.")
            return
        if not password or not password.strip():
            print("비밀번호를 입력해주세요.")
            return
        
         # 공백 제거
        student_id = student_id.strip()
        name = name.strip()
        phone = phone.strip()
        password = password.strip()
        
        # 학번 검증
        if not self._validate_student_id(student_id):
            return
        
        # 이름 검증
        if not self._validate_name(name):
            return
        
        # 연락처 검증
        if not self._validate_phone(phone):
            return
        
        # 비밀번호 검증
        if not self._validate_password(password):
            return
        
        
        # 중복 검사
        if any(m.student_id == student_id for m in self.repo.members):
            print("이미 등록된 학번입니다.")
            return
        
        # 연락처 중복 검사
        if any(m.phone == phone for m in self.repo.members):
            print("이미 등록된 연락처입니다.")
            return
        
        
        self.repo.members.append(Member(
            student_id=student_id, 
            name=name, 
            phone=phone, 
            password=password, 
            registered_date=date_str(self.today)
        ))
        self.repo.persist()
        print("회원 등록 완료")

    def list_members(self):
        if not self.repo.members:
            print("등록된 회원이 없습니다.")
            return
        print("\n[회원 목록]")
        print("학번 | 이름 | 연락처 | 등록일")
        print("-" * 50)
        for m in self.repo.members:
            print(f"  {m.student_id} | {m.name} | {m.phone} | {m.registered_date}")

    def remove_member(self, student_id: str):
        """회원을 탈퇴시킵니다. 대출중인 도서가 있으면 탈퇴할 수 없습니다."""
        # 회원 존재 확인
        member = None
        for m in self.repo.members:
            if m.student_id == student_id:
                member = m
                break
        
        if not member:
            print("존재하지 않는 학번입니다.")
            return
        
        # 대출중인 도서 확인
        active_loans = [l for l in self.repo.loans if l.student_id == student_id and l.return_date is None]
        if active_loans:
            print(f"대출중인 도서가 {len(active_loans)}권 있어 탈퇴할 수 없습니다.")
            print("대출중인 도서를 모두 반납한 후 탈퇴해주세요.")
            return
        
        # 회원 삭제
        self.repo.members.remove(member)
        self.repo.persist()
        print(f"회원 탈퇴 완료: {member.name} ({member.student_id})")

    # ---- 대출/반납 ----
    def loan(self, student_id: str, work_id: int):
        # 참조 무결성 검사 (FK 위반 시 완전히 금지)
        try:
            self._validate_fk(work_id=work_id, student_id=student_id)
        except ValueError as e:
            print(f"오류: {e}")
            print("대출이 거부되었습니다.")
            return
        
        # 회원 확인
        if not any(m.student_id == student_id for m in self.repo.members):
            print("회원이 아닙니다. 회원 등록 후 이용하세요.")
            return
        # 도서 확인
        work = self._find_work(work_id)
        if not work or work.deleted_date is not None:
            print("도서가 존재하지 않거나 삭제되었습니다.")
            return
        # 대출 가능한 복본 찾기
        cp = self._find_available_copy(work_id)
        if not cp:
            print("대출 가능한 복본이 없습니다.")
            return
        
        # 중복 대출 방지: 해당 복본이 이미 대출 중인지 확인
        active_loan = next((l for l in self.repo.loans if l.copy_id == cp.copy_id and l.return_date is None), None)
        if active_loan:
            print("해당 복본은 이미 대출 중입니다.")
            return
        
        # 대출 생성 (추가 FK 검사)
        try:
            self._validate_fk(copy_id=cp.copy_id)
        except ValueError as e:
            print(f"오류: {e}")
            print("대출이 거부되었습니다.")
            return
        
        cp.status = "loaned"
        loan = Loan(
            loan_id=self._next_loan_id,
            copy_id=cp.copy_id,
            work_id=work_id,
            student_id=student_id,
            loan_date=date_str(self.today),
            due_date=date_str(self.today + timedelta(days=DUE_DAYS)),
            return_date=None,
        )
        self.repo.loans.append(loan)
        self._next_loan_id += 1
        self.repo.persist()
        
        # 책 제목 가져오기
        book_title = work.title
        print(f"대출 완료: loan_id={loan.loan_id}, 제목='{book_title}', 반납기한={loan.due_date}")

    def return_copy(self, loan_id: int):
        loan = None
        for l in self.repo.loans:
            if l.loan_id == loan_id:
                loan = l
                break
        if not loan:
            print("해당 대출 기록이 없습니다.")
            return
        if loan.return_date is not None:
            print("이미 반납 처리된 대출입니다.")
            return
        
        # 참조 무결성 검사 (FK 위반 시 완전히 금지)
        try:
            self._validate_fk(copy_id=loan.copy_id, student_id=loan.student_id, work_id=loan.work_id)
        except ValueError as e:
            print(f"오류: {e}")
            print("반납이 거부되었습니다.")
            return
        
        # 복본 상태 복구
        cp = next((c for c in self.repo.copies if c.copy_id == loan.copy_id), None)
        if cp:
            # 삭제된 복본이라도 반납처리는 가능하지만 상태는 deleted 유지
            if cp.status == "loaned":
                # 삭제되지 않은 경우만 available로 되돌림
                cp.status = "available" if cp.deleted_date is None else cp.status
        loan.return_date = date_str(self.today)
        self.repo.persist()
        
        # 책 제목 가져오기
        work = self._find_work(loan.work_id)
        book_title = work.title if work else "알 수 없음"
        
        overdue = parse_date(loan.return_date) > parse_date(loan.due_date)
        if overdue:
            print(f"반납 완료(연체): loan_id={loan.loan_id}, 제목='{book_title}', 대출일={loan.loan_date}, 기한={loan.due_date}, 반납일={loan.return_date}")
        else:
            print(f"반납 완료: loan_id={loan.loan_id}, 제목='{book_title}', 반납일={loan.return_date}")

    def list_loans(self, only_open: bool = False):
        rows = []
        for l in self.repo.loans:
            if only_open and l.return_date is not None:
                continue
            overdue = (l.return_date is None and self.today > parse_date(l.due_date))
            # 책 제목 가져오기
            work = self._find_work(l.work_id)
            book_title = work.title if work else "알 수 없음"
            # 제목이 너무 길면 줄임
            if len(book_title) > 15:
                book_title = book_title[:12] + "..."
            rows.append((l.loan_id, l.student_id, book_title, l.copy_id, l.loan_date, l.due_date, l.return_date, overdue))
        if not rows:
            print("대출 기록이 없습니다.")
            return
        print("\n[대출 현황]")
        print("loan_id | 학번 | 제목 | copy_id | 대출일 | 기한 | 반납일 | 연체")
        print("-" * 90)
        for r in rows:
            overdue_mark = "Y" if r[7] else "N"
            print(f"  {r[0]:>3} | {r[1]} | {r[2]:<15} | {r[3]} | {r[4]} | {r[5]} | {r[6] or '-'} | {overdue_mark}")

# -------------------- 대화식 CLI --------------------

class CLI:
    def __init__(self, repo: Repository, today: date):
        self.repo = repo
        self.service = LibraryService(self.repo, today)
        self.logged_in = None  # (role, id)  role: 'admin' or 'member'

    def run(self):
        print("==== 도서 대출 프로그램 (CLI) ====")
        while True:
            if not self.logged_in:
                if not self._menu_welcome():
                    return  # 종료
            else:
                role, _ = self.logged_in
                if role == 'admin':
                    if not self._menu_admin():
                        return
                else:
                    if not self._menu_member():
                        return

    # --- 메뉴들 ---
    def _menu_welcome(self) -> bool:
        print("\n[미로그인 메뉴]")
        print(" 1) 로그인 (회원)")
        print(" 2) 로그인 (관리자)")
        print(" 3) 회원 등록")
        print(" 0) 종료")
        try:
            cmd = input("선택: ").strip()
        except OSError:
            print("입력이 불가능한 환경입니다.")
            return False
        if cmd == '1':
            student_id = input("학번: ").strip()
            password = input("비밀번호: ").strip()
            member = None
            for m in self.repo.members:
                if m.student_id == student_id and m.password == password:
                    member = m
                    break
            if member:
                self.logged_in = ('member', member.student_id)
                print(f"회원 로그인 완료: {member.name} ({member.student_id})")
            else:
                print("학번 또는 비밀번호가 잘못되었습니다.")
        elif cmd == '2':
            uid = input("관리자 ID: ").strip()
            pw = input("비밀번호: ").strip()
            if uid == 'admin' and pw == 'admin':
                # 관리자 로그인 후 날짜 입력 받기
                while True:
                    try:
                        user_input = input(f"오늘 날짜를 입력하세요 (기본값: {date_str(self.service.today)}, 엔터로 사용): ").strip()
                        
                        # 빈 입력인 경우 기본값 사용
                        if not user_input:
                            break
                        
                        # 날짜 파싱 시도
                        try:
                            input_date = parse_date(user_input)
                            # 과거 날짜 검증
                            if input_date < date.today():
                                print("과거 날짜를 입력하셨습니다. 다시 입력하세요.")
                                continue
                            self.service.set_today(input_date)
                            break
                        except:
                            print("형식은 YYYY-MM-DD 입니다.")
                            continue
                            
                    except OSError:
                        print("입력이 불가능한 환경으로 판단되어 종료합니다.")
                        return False
                
                self.logged_in = ('admin', 'admin')
                print("관리자 로그인 완료")
            else:
                print("인증 실패")
        elif cmd == '3':
            sid = input("학번: ").strip()
            name = input("이름: ").strip()
            phone = input("연락처: ").strip()
            password = input("비밀번호: ").strip()
            self.service.register_member(sid, name, phone, password)
        elif cmd == '0':
            print("프로그램을 종료합니다.")
            return False
        else:
            print("잘못된 입력입니다.")
        return True

    def _menu_admin(self) -> bool:
        print(f"\n[관리자 메뉴 @ {self.service.today}]")
        print(" 1) 도서 등록(+복본 수)")
        print(" 2) 도서 삭제")
        print(" 3) 도서 목록")
        print(" 4) 도서 검색")
        print(" 5) 회원 목록")
        print(" 6) 회원 탈퇴")
        print(" 7) 대출 현황(전체)")
        print(" 8) 대출 현황(미반납만)")
        print(" 9) 오늘 날짜 변경")
        print("10) 로그아웃")
        print(" 0) 종료")
        try:
            cmd = input("선택: ").strip()
        except OSError:
            print("입력이 불가능한 환경입니다.")
            return False
        if cmd == '1':
            title = input("제목: ").strip()
            author = input("저자: ").strip()
            try:
                copies = int(input("복본 수(기본 1): ") or "1")
                copies = max(1, copies)
            except ValueError:
                copies = 1
            self.service.add_work(title, author, copies)
        elif cmd == '2':
            try:
                wid = int(input("삭제할 work_id: ").strip())
            except ValueError:
                print("정수 work_id를 입력하세요.")
                return True
            self.service.delete_work(wid)
        elif cmd == '3':
            self.service.list_works()
        elif cmd == '4':
            kw = input("검색어(제목/저자): ").strip()
            self.service.search_works(kw)
        elif cmd == '5':
            self.service.list_members()
        elif cmd == '6':
            sid = input("탈퇴시킬 학번: ").strip()
            self.service.remove_member(sid)
        elif cmd == '7':
            self.service.list_loans(only_open=False)
        elif cmd == '8':
            self.service.list_loans(only_open=True)
        elif cmd == '9':
            nd = _input_date_safe(f"새 오늘 날짜(기본값: {date_str(self.service.today)}, 엔터로 현재 날짜 유지): ")
            if nd is not None:
                self.service.set_today(nd)
        elif cmd == '10':
            self.logged_in = None
            print("로그아웃되었습니다.")
        elif cmd == '0':
            print("프로그램을 종료합니다.")
            return False
        else:
            print("잘못된 입력입니다.")
        return True

    def _menu_member(self) -> bool:
        role, sid = self.logged_in
        print(f"\n[회원 메뉴 @ {self.service.today}] (학번 {sid})")
        print(" 1) 도서 목록")
        print(" 2) 도서 검색")
        print(" 3) 대출")
        print(" 4) 반납")
        print(" 5) 내 대출 현황")
        print(" 6) 로그아웃")
        print(" 0) 종료")
        try:
            cmd = input("선택: ").strip()
        except OSError:
            print("입력이 불가능한 환경입니다.")
            return False
        if cmd == '1':
            self.service.list_works()
        elif cmd == '2':
            kw = input("검색어(제목/저자): ").strip()
            self.service.search_works(kw)
        elif cmd == '3':
            try:
                wid = int(input("대출할 work_id: ").strip())
            except ValueError:
                print("정수 work_id를 입력하세요.")
                return True
            self.service.loan(student_id=sid, work_id=wid)
        elif cmd == '4':
            try:
                lid = int(input("반납할 loan_id: ").strip())
            except ValueError:
                print("정수 loan_id를 입력하세요.")
                return True
            self.service.return_copy(lid)
        elif cmd == '5':
            # 내 대출만 필터링
            all_loans = [l for l in self.repo.loans if l.student_id == sid]
            if not all_loans:
                print("대출 기록이 없습니다.")
            else:
                print("\n[내 대출 현황]")
                print("loan_id | 제목 | copy_id | 대출일 | 기한 | 반납일 | 연체")
                print("-" * 80)
                for l in all_loans:
                    # 책 제목 가져오기
                    work = self.service._find_work(l.work_id)
                    book_title = work.title if work else "알 수 없음"
                    # 제목이 너무 길면 줄임
                    if len(book_title) > 20:
                        book_title = book_title[:17] + "..."
                    
                    overdue = (l.return_date is None and self.service.today > parse_date(l.due_date))
                    mark = 'Y' if overdue else 'N'
                    print(f"  {l.loan_id:>3} | {book_title:<20} | {l.copy_id} | {l.loan_date} | {l.due_date} | {l.return_date or '-'} | {mark}")
        elif cmd == '6':
            self.logged_in = None
            print("로그아웃되었습니다.")
        elif cmd == '0':
            print("프로그램을 종료합니다.")
            return False
        else:
            print("잘못된 입력입니다.")
        return True

# -------------------- 자가 테스트 --------------------

def run_selftest(repo: Repository, today: date):
    """자가 테스트를 실행합니다."""
    service = LibraryService(repo, today)
    
    print("[SELFTEST] 시작 — 기본 흐름 검증")
    
    # 날짜 설정
    service.set_today(today)
    
    # 회원 등록
    service.register_member("20230001", "Alice", "010-1111-2222", "password123")
    service.register_member("20230002", "Bob", "010-3333-4444", "password123")
    
    # 도서 등록
    service.add_work("Clean Code", "Robert C. Martin", 2)
    service.add_work("The Pragmatic Programmer", "Andrew Hunt", 1)
    service.list_works()
    
    # 대출 테스트
    service.loan("20230001", 1)
    service.list_loans()
    
    # 반납 테스트
    service.return_copy(1)
    service.list_loans(only_open=True)
    
    # 검색 테스트
    service.search_works("Code")
    service.list_members()
    
    # 연체 상황 검증
    print("\n[SELFTEST] 연체 상황 검증")
    later = today + timedelta(days=DUE_DAYS + 1)
    service.set_today(later)
    
    # 새 대출
    service.loan("20230002", 1)
    service.list_loans(only_open=True)
    
    print("[SELFTEST] 완료 — 출력 로그를 확인해 주세요.")

# -------------------- 입력 보조 --------------------

def _input_date_safe(prompt: str, allow_past: bool = True) -> Optional[date]:
    """
    안전한 날짜 입력 함수
    allow_past: False인 경우 과거 날짜 입력을 허용하지 않음
    """
    try:
        s = input(prompt).strip()
    except OSError:
        print("입력이 불가능한 환경입니다. 날짜 변경을 건너뜁니다.")
        return None
    # 빈 입력인 경우 None을 반환하여 기본값 사용
    if not s:
        return None
    try:
        input_date = parse_date(s)
        # 과거 날짜 검증
        if not allow_past and input_date < date.today():
            print("과거 날짜를 입력하셨습니다. 다시 입력하세요.")
            return None
        return input_date
    except Exception:
        print("형식은 YYYY-MM-DD 입니다.")
        return None

# -------------------- 진입점 --------------------

def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="도서 대출 프로그램 (YES24 엑셀 데이터 기반)")
    parser.add_argument("--mode", choices=["interactive", "selftest"], default="interactive")
    parser.add_argument("--today", help="가상의 오늘 날짜(YYYY-MM-DD)")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="데이터 디렉터리 (기본: ./data)")
    args = parser.parse_args(argv)

    # today 결정
    if args.today:
        today = parse_date(args.today)
    else:
        today = date.today()

    # 저장소 준비
    repo = Repository(data_dir=args.data_dir)

    if args.mode == "interactive":
        CLI(repo, today).run()
        
    elif args.mode == "selftest":
        # 테스트는 별도 데이터 디렉터리를 사용하여 기존 데이터에 영향 없게 수행
        test_dir = os.path.join(args.data_dir, "_selftest")
        ensure_data_dir(test_dir)
        test_repo = Repository(data_dir=test_dir)
        run_selftest(test_repo, today)


if __name__ == "__main__":
    main()
