"""대진표 생성기.

1. 1인당 게임 수와 라운드 수를 정해요. (반드시 지킴: 게임 수 차이 1 이하)
2. 라운드마다 누가 뛸지 정한 초기 대진을 만들어요.
3. 담금질 기법(Simulated Annealing)으로 사람/팀을 바꿔가며 벌점 합을 줄여요.
   - 사람별 게임 수는 바뀌지 않는 이동만 써요.
   - 고정 파트너는 항상 같은 팀으로 움직여요.
"""
import itertools
import math
import random
import time
from collections import Counter
from dataclasses import dataclass, field

from models import Player, find_pairs

MODE_MIXED = "혼복 위주"
MODE_SAME = "남복·여복 위주"
MODE_FREE = "자유"
GENDER_MODES = [MODE_MIXED, MODE_SAME, MODE_FREE]

KIND_SAME = "같은 급수"
KIND_EVENT = "이벤트"

# 벌점 가중치: 우선순위가 높을수록 커요
W_THREE_IN_ROW = 3000   # ② 연속 게임 (길이-2)^2 : 3연속 1배, 4연속 4배, 5연속 9배
W_LONG_REST = 1000      # ② 연속 휴식 (길이-1)^2
W_CROSS = 2000          # ③ 허용하지 않은 성별 구성이 다른 대결
W_MODE = 800            # ③ 성별 모드와 다른 게임
W_BALANCE = 60          # ④ 양 팀 점수 차이^2
W_AGE = 2               # ④ 양 팀 평균 나이 차이^2 (10살 차이 = 200, 점수 2점 차이 = 240)
W_GRADE = 150           # ⑤ 같은 급수 게임에 다른 급수가 섞임 (급수 차이^2)
W_BORROW = 20           # ⑤ 빌려온 사람의 점수가 그 급수와 얼마나 다른지
W_AGE_GROUP = 3         # ⑤ 같은 급수 게임 4명의 나이 차가 AGE_GROUP_GAP을 넘은 만큼^2
AGE_GROUP_GAP = 10      #   (20살 차이 = 10살 넘음 = 300)
AGE_GROUP_CAP = 600     #   한 게임 최대치. W_MODE보다 작게 둬서 성별 모드(③)를 앞서지 않게 해요
W_EVENT_MIX = 40        # ⑤ 이벤트 게임 팀 안의 점수 차이가 작음
W_EVENT_SPREAD = 200    # ⑤ 사람마다 이벤트 게임 수가 고르지 않음
W_PARTNER = 30          # ⑥ 같은 파트너 반복
W_OPPONENT = 10         # ⑥ 같은 상대 반복

EVENT_MIN_DIFF = 3      # 이벤트 게임에서 팀원끼리 이 정도 점수 차이는 나야 '혼합'
REVIEW_SCORE_DIFF = 3   # 양 팀 점수 차이가 이 이상이면 '검토 필요 경기'로 알려줘요
REST_TIME_SHARE = 0.3   # 탐색 시간 중 1단계(휴식 패턴)에 쓰는 비율


@dataclass
class Settings:
    courts: int
    games_per_player: int
    gender_mode: str = MODE_MIXED
    allow_cross: bool = False
    event_ratio: float = 0.0   # 0.0 ~ 1.0
    time_limit: float = 5.0    # 탐색 시간(초)
    seed: int | None = None


@dataclass
class Game:
    round: int          # 1부터
    court: int          # 1부터
    kind: str           # 같은 급수 / 이벤트
    gender_type: str    # 남복 / 여복 / 혼복 / 혼성 대결
    team_a: tuple[Player, Player]
    team_b: tuple[Player, Player]

    @property
    def score_a(self) -> int:
        return sum(p.score for p in self.team_a)

    @property
    def score_b(self) -> int:
        return sum(p.score for p in self.team_b)

    @property
    def age_diff(self) -> float:
        """양 팀 평균 나이 차이."""
        return abs(sum(p.age for p in self.team_a) - sum(p.age for p in self.team_b)) / 2

    @property
    def age_spread(self) -> int:
        """4명 중 가장 많은 나이와 가장 적은 나이의 차이."""
        ages = [p.age for p in self.team_a + self.team_b]
        return max(ages) - min(ages)

    @property
    def needs_review(self) -> bool:
        """양 팀 실력 차이가 커서 사람이 한 번 봐야 하는 경기."""
        return abs(self.score_a - self.score_b) >= REVIEW_SCORE_DIFF


@dataclass
class PlayerStats:
    player: Player
    games: int = 0
    same_grade: int = 0
    event: int = 0
    men_doubles: int = 0
    women_doubles: int = 0
    mixed_doubles: int = 0
    cross: int = 0
    rests: int = 0
    max_streak: int = 0
    three_in_row: int = 0     # 3게임 이상 연속으로 뛴 횟수
    partner_repeats: int = 0
    opponent_repeats: int = 0
    pattern: str = ""         # ●=경기, ○=휴식


@dataclass
class Schedule:
    players: list[Player]
    settings: Settings
    rounds: list[list[Game]]
    resting: list[list[Player]]
    courts_used: int
    stats: list[PlayerStats] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def review_games(self) -> list[Game]:
        return [gm for rd in self.rounds for gm in rd if gm.needs_review]


class ScheduleError(Exception):
    pass


# 8명을 4명씩 두 게임으로 나누는 35가지 (0번은 항상 첫 게임)
_SPLITS_8 = [c for c in itertools.combinations(range(8), 4) if c[0] == 0]


def _team_splits(four, partner_of):
    """4명을 2:2로 나누는 3가지 중 고정 파트너를 지키는 것만."""
    a, b, c, d = four
    out = []
    for t1, t2 in (((a, b), (c, d)), ((a, c), (b, d)), ((a, d), (b, c))):
        if all(partner_of[p] < 0 or partner_of[p] in t for t in (t1, t2) for p in t):
            out.append((t1, t2))
    return out


def pattern_cost(seq: list[bool]) -> int:
    """경기(True)/휴식(False) 순서의 벌점: 긴 연속 경기와 긴 연속 휴식."""
    cost, run_play, run_rest = 0, 0, 0
    for x in list(seq) + [None]:
        if x is not False and run_rest > 1:
            cost += W_LONG_REST * (run_rest - 1) ** 2
        if x is not True and run_play > 2:
            cost += W_THREE_IN_ROW * (run_play - 2) ** 2
        if x:
            run_play, run_rest = run_play + 1, 0
        else:
            run_play, run_rest = 0, run_rest + 1
    return cost


# ---------------------------------------------------------------------------
# 게임 수 / 라운드 수
# ---------------------------------------------------------------------------

def plan_counts(n_players, courts, games_per_player, pairs, rng, prefer=None):
    """사용 코트, 라운드 수, 사람별 게임 수를 정해요.

    prefer(counts)가 주어지면 여러 번 뽑아서 값이 가장 작은 배정을 골라요.
    """
    if prefer is None:
        return _plan_counts_once(n_players, courts, games_per_player, pairs, rng)
    best = None
    for _ in range(200):
        plan = _plan_counts_once(n_players, courts, games_per_player, pairs, rng)
        value = prefer(plan[2])
        if best is None or value < best[0]:
            best = (value, plan)
        if value == 0:
            break
    return best[1]


def _plan_counts_once(n_players, courts, games_per_player, pairs, rng):
    courts_used = min(courts, n_players // 4)
    if courts_used < 1:
        raise ScheduleError("4명 이상 있어야 복식 게임을 짤 수 있어요")
    slots_per_round = courts_used * 4
    rounds = math.ceil(n_players * games_per_player / slots_per_round)
    extra = rounds * slots_per_round - n_players * games_per_player

    paired = {i for pair in pairs for i in pair}
    singles = [i for i in range(n_players) if i not in paired]
    rng.shuffle(singles)
    pair_list = list(pairs)
    rng.shuffle(pair_list)

    # 남는 코트를 채우려고 extra명이 한 게임 더 해요. 고정 파트너는 둘이 같이 받아요.
    k = min(extra, len(singles))
    while k >= 0 and ((extra - k) % 2 or (extra - k) // 2 > len(pair_list)):
        k -= 1
    if k < 0:
        raise ScheduleError("게임 수를 고르게 나눌 수 없어요. 인원이나 게임 수를 바꿔주세요")

    counts = [games_per_player] * n_players
    for i in singles[:k]:
        counts[i] += 1
    for a, b in pair_list[:(extra - k) // 2]:
        counts[a] += 1
        counts[b] += 1
    return courts_used, rounds, counts


# ---------------------------------------------------------------------------
# 최적화
# ---------------------------------------------------------------------------

class _Optimizer:
    def __init__(self, players: list[Player], settings: Settings):
        self.players = players
        self.s = settings
        self.rng = random.Random(settings.seed)
        self.progress = None
        n = len(players)

        pairs, errors = find_pairs(players)
        if errors:
            raise ScheduleError("\n".join(errors))
        self.partner_of = [-1] * n
        for a, b in pairs:
            self.partner_of[a] = b
            self.partner_of[b] = a
        self.singles = [i for i in range(n) if self.partner_of[i] < 0]

        self.score = [p.score for p in players]
        self.age = [p.age for p in players]
        self.level = [p.level for p in players]
        self.male = [p.gender == "남" for p in players]

        def prefer(counts):
            # 한 게임 더 할 사람을 성별 모드에 유리하게 골라요
            men = sum(c for c, m in zip(counts, self.male) if m)
            women = sum(counts) - men
            if settings.gender_mode == MODE_SAME:
                return men % 4
            if settings.gender_mode == MODE_MIXED:
                # 남자 출전 수가 홀수면 어느 라운드엔 혼성 대결이 생겨요
                return (men % 2) * 1000 + abs(men - women)
            return 0

        self.courts, self.R, self.counts = plan_counts(
            n, settings.courts, settings.games_per_player, pairs, self.rng, prefer)
        self.units = [(i,) for i in self.singles] + [tuple(p) for p in pairs]

        # 라운드별 이벤트 게임 수를 고르게 나눠요
        total_games = self.R * self.courts
        n_event = round(total_games * settings.event_ratio)
        self.slot_kind = []
        for r in range(self.R):
            e = round((r + 1) * n_event / self.R) - round(r * n_event / self.R)
            self.slot_kind.append([KIND_EVENT] * e + [KIND_SAME] * (self.courts - e))
        self.event_target = [c * n_event / total_games for c in self.counts]

    # ----- 초기 대진 -----------------------------------------------------

    def _initial_rest(self):
        """라운드별로 뛸 사람을 고르는 탐욕 배정 (쉬는 간격이 고르게)."""
        slots = self.courts * 4
        for attempt in range(1000):
            noise = 0.05 + attempt * 0.01
            remaining = {u: self.counts[u[0]] for u in self.units}
            last = {u: [] for u in self.units}
            plays = []
            ok = True
            for r in range(self.R):
                left = self.R - r
                must = [u for u in self.units if remaining[u] == left]
                rest = [u for u in self.units if 0 < remaining[u] < left]

                def priority(u):
                    p = remaining[u] / left
                    h = last[u]
                    if h[-1:] == [False]:
                        p += 0.5
                    if h[-2:] == [True, True]:
                        p -= 0.7
                    return p + self.rng.random() * noise

                rest.sort(key=priority, reverse=True)
                chosen = list(must)
                filled = sum(len(u) for u in chosen)
                for u in rest:
                    if filled + len(u) <= slots:
                        chosen.append(u)
                        filled += len(u)
                if filled != slots:
                    ok = False
                    break
                chosen_set = set(chosen)
                for u in self.units:
                    last[u].append(u in chosen_set)
                    if u in chosen_set:
                        remaining[u] -= 1
                plays.append(chosen)
            if ok:
                return plays
        raise ScheduleError("초기 대진을 만들지 못했어요. 설정을 바꿔서 다시 시도해주세요")

    def _round_gender_cost(self, men, women):
        """라운드에 뛰는 남녀 인원만 보고 성별 모드 벌점을 어림해요."""
        mode = self.s.gender_mode
        cross = W_MODE if self.s.allow_cross else W_CROSS
        if mode == MODE_FREE:
            return 0
        if mode == MODE_MIXED:
            k = min(men // 2, women // 2, self.courts)
            rm = men - 2 * k
            return W_MODE * (self.courts - k) + (cross - W_MODE if rm % 4 else 0)
        lm = men % 4
        if lm == 0:
            return 0
        return W_MODE if lm == 2 else cross

    def _plan_rest(self, plays):
        """1단계: 휴식 패턴만 담금질 (쉬는 간격 + 라운드별 남녀 인원)."""
        R, rng = self.R, self.rng
        units = self.units
        play = [[u in set(rd) for rd in plays] for u in units]
        n_male = [sum(self.male[p] for p in u) for u in units]

        def unit_cost(i):
            return pattern_cost(play[i]) * len(units[i])

        men = [sum(n_male[i] for i in range(len(units)) if play[i][r]) for r in range(R)]
        size = self.courts * 4
        ucost = [unit_cost(i) for i in range(len(units))]
        rcost = [self._round_gender_cost(men[r], size - men[r]) for r in range(R)]
        current = sum(ucost) + sum(rcost)

        best, best_play = current, [row[:] for row in play]

        start = time.perf_counter()
        limit = max(self.s.time_limit * REST_TIME_SHARE, 0.05)
        it, frac = 0, 0.0
        while True:
            it += 1
            if it % 128 == 0:
                frac = (time.perf_counter() - start) / limit
                if frac >= 1:
                    break
                if self.progress and it % 4096 == 0:
                    self.progress(REST_TIME_SHARE * frac)
            temp = 3000 * (0.0005 ** frac)
            # i는 r1에 뛰고 r2에 쉬고, j는 그 반대일 때 서로 바꿔요
            i = rng.randrange(len(units))
            r1, r2 = rng.randrange(R), rng.randrange(R)
            if not play[i][r1] or play[i][r2]:
                continue
            cand = [j for j in range(len(units)) if play[j][r2] and not play[j][r1]]
            same = [j for j in cand if len(units[j]) == len(units[i])]
            ones = [j for j in cand if len(units[j]) == 1]
            # 같은 크기끼리, 또는 고정 파트너 한 쌍 ↔ 개인 두 명
            if len(units[i]) == 2 and len(ones) >= 2 and (not same or rng.random() < 0.5):
                js = rng.sample(ones, 2)
            elif same:
                js = [rng.choice(same)]
            else:
                continue
            group = [i] + js
            old_u = [ucost[k] for k in group]
            old_r = (rcost[r1], rcost[r2], men[r1], men[r2])

            def flip():
                for k in group:
                    play[k][r1], play[k][r2] = play[k][r2], play[k][r1]
            flip()
            dm = sum(n_male[j] for j in js) - n_male[i]
            men[r1] += dm
            men[r2] -= dm
            for k in group:
                ucost[k] = unit_cost(k)
            rcost[r1] = self._round_gender_cost(men[r1], size - men[r1])
            rcost[r2] = self._round_gender_cost(men[r2], size - men[r2])
            new = (current + sum(ucost[k] for k in group) - sum(old_u)
                   + rcost[r1] + rcost[r2] - old_r[0] - old_r[1])
            if new <= current or rng.random() < math.exp((current - new) / temp):
                current = new
                if current < best:
                    best, best_play = current, [row[:] for row in play]
            else:
                flip()
                for k, c in zip(group, old_u):
                    ucost[k] = c
                rcost[r1], rcost[r2], men[r1], men[r2] = old_r
        return [[u for i, u in enumerate(units) if best_play[i][r]] for r in range(R)]

    def _team_type(self, team):
        return self.male[team[0]] + self.male[team[1]]  # 0=여여, 1=남녀, 2=남남

    def _initial_rounds(self):
        """성별 모드에 맞춰 팀을 짜고, 같은 구성의 팀끼리 붙여요."""
        rng = self.rng
        mode = self.s.gender_mode
        rounds = []
        for chosen in self._plan_rest(self._initial_rest()):
            teams = [list(u) for u in chosen if len(u) == 2]
            men = [u[0] for u in chosen if len(u) == 1 and self.male[u[0]]]
            women = [u[0] for u in chosen if len(u) == 1 and not self.male[u[0]]]
            rng.shuffle(men)
            rng.shuffle(women)
            if mode == MODE_MIXED:
                while men and women:
                    teams.append([men.pop(), women.pop()])
            elif mode == MODE_SAME:
                # 남녀 팀 수를 짝수로 맞추면 혼성 대결 대신 혼복이 돼요
                mixed_pairs = sum(1 for t in teams if self._team_type(t) == 1)
                if len(men) % 2 and len(women) % 2:
                    teams.append([men.pop(), women.pop()])
                    mixed_pairs += 1
                if mixed_pairs % 2 and len(men) >= 2 and len(women) >= 2:
                    teams.append([men.pop(), women.pop()])
                    teams.append([men.pop(), women.pop()])
            rest = men + women
            if mode == MODE_FREE:
                rng.shuffle(rest)
            teams += [rest[i:i + 2] for i in range(0, len(rest), 2)]

            rng.shuffle(teams)
            teams.sort(key=self._team_type)
            rounds.append([[teams[i], teams[i + 1]] for i in range(0, len(teams), 2)])
        return rounds

    # ----- 벌점 계산 -----------------------------------------------------

    def _gender_type(self, team_a, team_b):
        def t(team):
            m = self.male[team[0]] + self.male[team[1]]
            return ("여복", "혼복", "남복")[m]
        a, b = t(team_a), t(team_b)
        return a if a == b else "혼성 대결"

    def _game_cost(self, game, kind):
        a, b = game
        sc = self.score
        cost = W_BALANCE * (sc[a[0]] + sc[a[1]] - sc[b[0]] - sc[b[1]]) ** 2
        ag = self.age
        cost += W_AGE * ((ag[a[0]] + ag[a[1]] - ag[b[0]] - ag[b[1]]) / 2) ** 2

        mode = self.s.gender_mode
        if mode != MODE_FREE:
            gt = self._gender_type(a, b)
            if gt == "혼성 대결":
                cost += W_MODE if self.s.allow_cross else W_CROSS
            elif (mode == MODE_MIXED) != (gt == "혼복"):
                cost += W_MODE

        four = a + b
        if kind == KIND_SAME:
            levels = [self.level[p] for p in four]
            main = Counter(levels).most_common()
            # 가장 많은 급수를 기준으로 (동률이면 높은 급수)
            top = max(c for _, c in main)
            base = max(lv for lv, c in main if c == top)
            base_scores = [sc[p] for p in four if self.level[p] == base]
            base_avg = sum(base_scores) / len(base_scores)
            for p in four:
                d = self.level[p] - base
                if d:
                    cost += W_GRADE * d * d + W_BORROW * abs(sc[p] - base_avg)
            ages = [self.age[p] for p in four]
            over = max(ages) - min(ages) - AGE_GROUP_GAP
            if over > 0:
                cost += min(W_AGE_GROUP * over * over, AGE_GROUP_CAP)
        else:
            for t in (a, b):
                gap = EVENT_MIN_DIFF - abs(sc[t[0]] - sc[t[1]])
                if gap > 0:
                    cost += W_EVENT_MIX * gap * gap
        return cost

    def _round_info(self, r):
        """라운드 r의 (게임 벌점, 파트너 쌍, 상대 쌍, 이벤트 참가자)."""
        cost = 0
        partners, opponents, event_players = [], [], []
        for g, game in enumerate(self.rounds[r]):
            kind = self.slot_kind[r][g]
            cost += self._game_cost(game, kind)
            a, b = game
            for t in (a, b):
                if self.partner_of[t[0]] != t[1]:
                    partners.append((min(t), max(t)))
            for x in a:
                for y in b:
                    opponents.append((min(x, y), max(x, y)))
            if kind == KIND_EVENT:
                event_players.extend(a + b)
        return cost, partners, opponents, event_players

    def _rest_cost(self, p):
        return pattern_cost([p in m for m in self.members])

    def _members(self, r):
        return {p for game in self.rounds[r] for team in game for p in team}

    # 반복(파트너/상대)과 이벤트 분배는 전체 카운터로 관리해요
    def _apply_info(self, info, sign):
        _, partners, opponents, event_players = info
        delta = 0
        for key in partners:
            k = self.partner_cnt[key]
            delta += W_PARTNER * ((max(k + sign - 1, 0)) ** 2 - (max(k - 1, 0)) ** 2)
            self.partner_cnt[key] = k + sign
        for key in opponents:
            k = self.opp_cnt[key]
            delta += W_OPPONENT * ((max(k + sign - 1, 0)) ** 2 - (max(k - 1, 0)) ** 2)
            self.opp_cnt[key] = k + sign
        for p in event_players:
            t = self.event_target[p]
            k = self.ev_cnt[p]
            delta += W_EVENT_SPREAD * ((k + sign - t) ** 2 - (k - t) ** 2)
            self.ev_cnt[p] = k + sign
        self.global_cost += delta

    def _init_state(self):
        self.rounds = self._initial_rounds()
        self.members = [self._members(r) for r in range(self.R)]
        self.partner_cnt = Counter()
        self.opp_cnt = Counter()
        self.ev_cnt = [0] * len(self.players)
        self.global_cost = sum(W_EVENT_SPREAD * t * t for t in self.event_target)
        self.info = [self._round_info(r) for r in range(self.R)]
        for info in self.info:
            self._apply_info(info, +1)
        self.rest = [self._rest_cost(p) for p in range(len(self.players))]

    def total(self):
        return sum(i[0] for i in self.info) + sum(self.rest) + self.global_cost

    # ----- 이동 ---------------------------------------------------------

    def _find(self, r, p):
        for g, game in enumerate(self.rounds[r]):
            for t, team in enumerate(game):
                if p in team:
                    return g, t, team.index(p)
        raise KeyError(p)

    def _keep_gender(self):
        """성별 모드가 있으면 대부분은 같은 성별끼리만 바꿔요 (성별 구성 유지)."""
        return self.s.gender_mode != MODE_FREE and self.rng.random() < 0.8

    def _best_regroup(self, r, g1, g2):
        """라운드 r의 두 게임(8명)을 가능한 모든 조합으로 다시 짜서 가장 좋은 걸 찾아요."""
        eight = [p for g in (g1, g2) for team in self.rounds[r][g] for p in team]
        kinds = (self.slot_kind[r][g1], self.slot_kind[r][g2])
        best, best_cfg = None, None
        for group in _SPLITS_8:
            four_a = [eight[i] for i in group]
            four_b = [p for p in eight if p not in four_a]
            games_a = [g for g in _team_splits(four_a, self.partner_of)]
            games_b = [g for g in _team_splits(four_b, self.partner_of)]
            orders = ((games_a, games_b), (games_b, games_a)) if kinds[0] != kinds[1] \
                else ((games_a, games_b),)
            for first, second in orders:
                for ga in first:
                    ca = self._game_cost(ga, kinds[0])
                    if best is not None and ca >= best:
                        continue
                    for gb in second:
                        c = ca + self._game_cost(gb, kinds[1])
                        if best is None or c < best:
                            best, best_cfg = c, (ga, gb)
        return best_cfg

    def _propose(self):
        """이동 하나를 고르고 (영향받는 라운드, 영향받는 사람, 실행 함수)를 돌려줘요."""
        rng = self.rng
        roll = rng.random()
        R, C = self.R, self.courts

        if roll < 0.04:  # 두 게임 통째로 다시 짜기 (가장 좋은 조합)
            if C < 2:
                return None
            r = rng.randrange(R)
            g1, g2 = rng.sample(range(C), 2)
            cfg = self._best_regroup(r, g1, g2)
            if cfg is None:
                return None

            def do():
                self.rounds[r][g1] = [list(t) for t in cfg[0]]
                self.rounds[r][g2] = [list(t) for t in cfg[1]]
            return (r,), (), do

        if roll < 0.40:  # 같은 라운드에서 두 사람 자리 바꾸기
            r = rng.randrange(R)
            pos = [(g, t, k) for g in range(C) for t in range(2) for k in range(2)
                   if self.partner_of[self.rounds[r][g][t][k]] < 0]
            if len(pos) < 2:
                return None
            x = rng.choice(pos)
            px = self.rounds[r][x[0]][x[1]][x[2]]
            if self._keep_gender():
                pos = [q for q in pos if self.male[self.rounds[r][q[0]][q[1]][q[2]]] == self.male[px]]
            y = rng.choice(pos)
            if x[:2] == y[:2]:
                return None

            def do():
                rd = self.rounds[r]
                rd[x[0]][x[1]][x[2]], rd[y[0]][y[1]][y[2]] = \
                    rd[y[0]][y[1]][y[2]], rd[x[0]][x[1]][x[2]]
            return (r,), (), do

        if roll < 0.55:  # 같은 라운드에서 팀 통째로 바꾸기
            if C < 2:
                return None
            r = rng.randrange(R)
            g1, g2 = rng.sample(range(C), 2)
            t1, t2 = rng.randrange(2), rng.randrange(2)

            def do():
                rd = self.rounds[r]
                rd[g1][t1], rd[g2][t2] = rd[g2][t2], rd[g1][t1]
            return (r,), (), do

        if R < 2:
            return None

        if roll < 0.90:  # 두 사람이 뛰는 라운드를 서로 바꾸기
            if not self.singles:
                return None
            u = rng.choice(self.singles)
            play = [r for r in range(R) if u in self.members[r]]
            rest = [r for r in range(R) if u not in self.members[r]]
            if not play or not rest:
                return None
            r1, r2 = rng.choice(play), rng.choice(rest)
            cand = [v for v in self.members[r2]
                    if self.partner_of[v] < 0 and v not in self.members[r1]]
            if self._keep_gender():
                cand = [v for v in cand if self.male[v] == self.male[u]]
            if not cand:
                return None
            v = rng.choice(cand)

            def do():
                g, t, k = self._find(r1, u)
                self.rounds[r1][g][t][k] = v
                g, t, k = self._find(r2, v)
                self.rounds[r2][g][t][k] = u
                self.members[r1].remove(u)
                self.members[r1].add(v)
                self.members[r2].remove(v)
                self.members[r2].add(u)
            return (r1, r2), (u, v), do

        # 두 팀이 뛰는 라운드를 서로 바꾸기 (고정 파트너용)
        r1, r2 = rng.sample(range(R), 2)
        g1, t1 = rng.randrange(C), rng.randrange(2)
        g2, t2 = rng.randrange(C), rng.randrange(2)
        team1 = self.rounds[r1][g1][t1]
        team2 = self.rounds[r2][g2][t2]
        if any(p in self.members[r2] for p in team1) or \
                any(p in self.members[r1] for p in team2):
            return None

        def do():
            self.rounds[r1][g1][t1], self.rounds[r2][g2][t2] = team2, team1
            for p in team1:
                self.members[r1].remove(p)
                self.members[r2].add(p)
            for p in team2:
                self.members[r2].remove(p)
                self.members[r1].add(p)
        return (r1, r2), tuple(team1) + tuple(team2), do

    # ----- 담금질 ---------------------------------------------------------

    def _snapshot(self):
        return [[[list(t) for t in game] for game in rd] for rd in self.rounds]

    def run(self, progress=None):
        """progress(0~1)로 진행률을 알려줘요. 1단계가 앞 30%, 2단계가 나머지예요."""
        self.progress = progress
        self._init_state()
        current = self.total()
        best, best_rounds = current, self._snapshot()

        # 1단계에서 맞춘 휴식 패턴이 흐트러지지 않게 낮은 온도에서 시작해요
        t_start, t_end = 300.0, 0.5
        start = time.perf_counter()
        limit = max(self.s.time_limit * (1 - REST_TIME_SHARE), 0.1)
        it = 0
        while True:
            it += 1
            if it % 256 == 0:
                frac = (time.perf_counter() - start) / limit
                if frac >= 1 or best == 0:
                    break
                temp = t_start * (t_end / t_start) ** frac
                if progress and it % 4096 == 0:
                    progress(REST_TIME_SHARE + (1 - REST_TIME_SHARE) * frac)
            elif it == 1:
                temp = t_start

            move = self._propose()
            if move is None:
                continue
            rs, ps, do = move

            saved_rounds = {r: [[list(t) for t in game] for game in self.rounds[r]] for r in rs}
            saved_members = {r: set(self.members[r]) for r in rs}
            saved_info = {r: self.info[r] for r in rs}
            saved_rest = {p: self.rest[p] for p in ps}
            saved_global = self.global_cost

            for r in rs:
                self._apply_info(self.info[r], -1)
            do()
            for r in rs:
                self.info[r] = self._round_info(r)
                self._apply_info(self.info[r], +1)
            for p in ps:
                self.rest[p] = self._rest_cost(p)
            new = self.total()

            delta = new - current
            if delta <= 0 or self.rng.random() < math.exp(-delta / temp):
                current = new
                if current < best:
                    best, best_rounds = current, self._snapshot()
            else:
                for r in rs:
                    self._apply_info(self.info[r], -1)
                for r in rs:
                    self.rounds[r] = saved_rounds[r]
                    self.members[r] = saved_members[r]
                    self.info[r] = saved_info[r]
                    self._apply_info(self.info[r], +1)
                for p in ps:
                    self.rest[p] = saved_rest[p]
                self.global_cost = saved_global  # 부동소수 오차 방지

        self.rounds = best_rounds
        self.iterations = it
        return best


# ---------------------------------------------------------------------------
# 결과 만들기
# ---------------------------------------------------------------------------

def _build_schedule(opt: _Optimizer, players, settings) -> Schedule:
    rounds, resting = [], []
    for r, rd in enumerate(opt.rounds):
        games = []
        for g, (a, b) in enumerate(rd):
            kind = opt.slot_kind[r][g]
            # 점수가 높은 팀을 왼쪽에, 팀 안에서도 점수 높은 사람 먼저
            ta = tuple(sorted((players[i] for i in a), key=lambda p: -p.score))
            tb = tuple(sorted((players[i] for i in b), key=lambda p: -p.score))
            if sum(p.score for p in tb) > sum(p.score for p in ta):
                ta, tb = tb, ta
            games.append(Game(r + 1, 0, kind, opt._gender_type(a, b), ta, tb))
        # 같은 급수 게임은 높은 급수부터, 이벤트 게임은 뒤 코트로
        games.sort(key=lambda gm: (gm.kind == KIND_EVENT, -(gm.score_a + gm.score_b)))
        for c, gm in enumerate(games):
            gm.court = c + 1
        rounds.append(games)
        playing = {p.name for gm in games for p in gm.team_a + gm.team_b}
        resting.append([p for p in players if p.name not in playing])

    sched = Schedule(players, settings, rounds, resting, opt.courts)
    sched.stats = compute_stats(sched)
    sched.summary = compute_summary(sched)
    return sched


def compute_stats(sched: Schedule) -> list[PlayerStats]:
    stats = {p.name: PlayerStats(p) for p in sched.players}
    partner_cnt, opp_cnt = Counter(), Counter()
    pattern = {p.name: [] for p in sched.players}

    for games in sched.rounds:
        playing = set()
        for gm in games:
            for team, other in ((gm.team_a, gm.team_b), (gm.team_b, gm.team_a)):
                for p in team:
                    s = stats[p.name]
                    playing.add(p.name)
                    s.games += 1
                    if gm.kind == KIND_EVENT:
                        s.event += 1
                    else:
                        s.same_grade += 1
                    if gm.gender_type == "남복":
                        s.men_doubles += 1
                    elif gm.gender_type == "여복":
                        s.women_doubles += 1
                    elif gm.gender_type == "혼복":
                        s.mixed_doubles += 1
                    else:
                        s.cross += 1
                    mate = team[1] if team[0] is p else team[0]
                    if p.partner != mate.name and mate.partner != p.name:
                        partner_cnt[(p.name, mate.name)] += 1
                    for o in other:
                        opp_cnt[(p.name, o.name)] += 1
        for p in sched.players:
            pattern[p.name].append(p.name in playing)

    for name, s in stats.items():
        seq = pattern[name]
        s.pattern = "".join("●" if x else "○" for x in seq)
        s.rests = seq.count(False)
        run = 0
        for x in seq + [False]:
            if x:
                run += 1
            else:
                if run >= 3:
                    s.three_in_row += 1
                s.max_streak = max(s.max_streak, run)
                run = 0
    for (a, _), k in partner_cnt.items():
        stats[a].partner_repeats += k - 1
    for (a, _), k in opp_cnt.items():
        stats[a].opponent_repeats += k - 1
    return list(stats.values())


def compute_summary(sched: Schedule) -> dict:
    games = [gm for rd in sched.rounds for gm in rd]
    ages = [gm.age_diff for gm in games]
    spreads = [gm.age_spread for gm in games if gm.kind == KIND_SAME] or [0]
    n_review = len(sched.review_games)
    counts = [s.games for s in sched.stats]
    gtypes = Counter(gm.gender_type for gm in games)
    kinds = Counter(gm.kind for gm in games)
    return {
        "참가 인원": len(sched.players),
        "사용 코트": sched.courts_used,
        "라운드 수": len(sched.rounds),
        "총 게임 수": len(games),
        "1인당 게임 수": f"{min(counts)} ~ {max(counts)}",
        "같은 급수 / 이벤트": f"{kinds[KIND_SAME]} / {kinds[KIND_EVENT]}",
        "남복 / 여복 / 혼복 / 혼성 대결":
            f"{gtypes['남복']} / {gtypes['여복']} / {gtypes['혼복']} / {gtypes['혼성 대결']}",
        "검토 필요 경기 (양 팀 실력 차이 큼)": f"{n_review}게임" if n_review else "없음",
        "양 팀 평균 나이 차 평균": round(sum(ages) / len(ages), 1),
        "양 팀 평균 나이 차 최대": max(ages),
        "같은 급수 게임 4명 나이 차 평균": round(sum(spreads) / len(spreads), 1),
        "같은 급수 게임 4명 나이 차 최대": max(spreads),
        "연속 3게임 이상 인원": sum(1 for s in sched.stats if s.three_in_row),
        "파트너 중복 (총합)": sum(s.partner_repeats for s in sched.stats) // 2,
    }


def generate(players: list[Player], settings: Settings, progress=None) -> Schedule:
    if settings.courts < 1:
        raise ScheduleError("코트 수는 1 이상이어야 해요")
    if settings.games_per_player < 1:
        raise ScheduleError("1인당 게임 수는 1 이상이어야 해요")
    opt = _Optimizer(players, settings)
    opt.run(progress)
    return _build_schedule(opt, players, settings)
