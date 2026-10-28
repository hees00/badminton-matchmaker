"""참가자 정보와 보정 점수 계산."""
from dataclasses import dataclass

GRADES = ["A", "B", "C", "D", "초심"]
GENDERS = ["남", "여"]

BASE_SCORE = {"A": 6, "B": 4, "C": 3, "D": 2, "초심": 1}
# 같은 급수 게임에서 급수 차이를 잴 때 쓰는 단계 값
GRADE_LEVEL = {"A": 4, "B": 3, "C": 2, "D": 1, "초심": 0}

MALE_BONUS = 2
YOUNG_MAX_AGE = 39   # 이 나이 이하 +1 (10대 포함)
SENIOR_MIN_AGE = 60  # 이 나이 이상 -1


def calc_score(gender: str, age: int, grade: str) -> int:
    """보정 점수 = 기본 점수 + 남자 보정 + 나이 보정 (초심은 항상 1점)."""
    if grade == "초심":
        return BASE_SCORE["초심"]
    score = BASE_SCORE[grade]
    if gender == "남":
        score += MALE_BONUS
    if age <= YOUNG_MAX_AGE:
        score += 1
    elif age >= SENIOR_MIN_AGE:
        score -= 1
    return score


@dataclass
class Player:
    name: str
    age: int
    gender: str
    grade: str
    partner: str = ""  # 고정 파트너 이름 (없으면 빈 문자열)

    @property
    def score(self) -> int:
        return calc_score(self.gender, self.age, self.grade)

    @property
    def level(self) -> int:
        return GRADE_LEVEL[self.grade]


def normalize_gender(value) -> str:
    v = str(value).strip().lower()
    if v in ("남", "남자", "m", "male", "man"):
        return "남"
    if v in ("여", "여자", "f", "female", "woman"):
        return "여"
    raise ValueError(f"성별을 알 수 없어요: {value!r} (남/여)")


def normalize_grade(value) -> str:
    v = str(value).strip().upper().replace("급", "")
    if v in ("A", "B", "C", "D"):
        return v
    if v in ("초심", "초보", "E", "F"):
        return "초심"
    raise ValueError(f"급수를 알 수 없어요: {value!r} (A/B/C/D/초심)")


def make_player(name, age, gender, grade, partner="") -> Player:
    name = str(name).strip()
    if not name:
        raise ValueError("이름이 비어 있어요")
    try:
        age = int(float(str(age).strip()))
    except ValueError:
        raise ValueError(f"{name}: 나이가 숫자가 아니에요 ({age!r})")
    partner = "" if partner is None else str(partner).strip()
    return Player(name, age, normalize_gender(gender), normalize_grade(grade), partner)


def find_pairs(players: list[Player]) -> tuple[list[tuple[int, int]], list[str]]:
    """고정 파트너 쌍(인덱스)과 오류 목록을 돌려줘요.

    한쪽만 상대 이름을 적어도 짝으로 인정해요.
    """
    errors = []
    index = {}
    for i, p in enumerate(players):
        if p.name in index:
            errors.append(f"이름이 중복돼요: {p.name}")
        index[p.name] = i

    partner_of = {}
    for i, p in enumerate(players):
        if not p.partner:
            continue
        if p.partner == p.name:
            errors.append(f"{p.name}: 자기 자신을 파트너로 지정했어요")
            continue
        j = index.get(p.partner)
        if j is None:
            errors.append(f"{p.name}: 파트너 '{p.partner}'가 명단에 없어요")
            continue
        for a, b in ((i, j), (j, i)):
            if partner_of.get(a, b) != b:
                errors.append(f"{players[a].name}: 파트너가 두 명 이상 지정됐어요")
            partner_of[a] = b

    pairs = sorted({(min(a, b), max(a, b)) for a, b in partner_of.items()})
    return pairs, errors
