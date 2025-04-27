# ofc_evaluator_5card.py v1.3
"""
Оценка 5-карточной руки OFC + генерация таблиц поиска.
Исправлен порядок генерации несочетанных рук, чтобы тесты выдавали ожидаемые ранги.
"""
import itertools
import logging
from typing import Dict, List, Generator, Optional

# Импортируем Card и PRIMES из ofc_logic
try:
    from ofc_logic import Card, PRIMES, INT_RANKS, INVALID_CARD
except ImportError:
    logging.critical("Failed to import dependencies in ofc_evaluator_5card.py")
    raise

logger = logging.getLogger(__name__)

class LookupTable5Card:
    """Хранит границы и генерирует lookup для 5-карточных рук."""
    MAX_STRAIGHT_FLUSH: int = 10
    MAX_FOUR_OF_A_KIND: int = 166
    MAX_FULL_HOUSE: int = 322
    MAX_FLUSH: int = 1599
    MAX_STRAIGHT: int = 1609
    MAX_THREE_OF_A_KIND: int = 2467
    MAX_TWO_PAIR: int = 3325
    MAX_PAIR: int = 6185
    MAX_HIGH_CARD: int = 7462
    WORST_RANK_5CARD: int = MAX_HIGH_CARD + 1

    RANK_CLASS_TO_STRING: Dict[int, str] = {
        1: "Straight Flush", 2: "Four of a Kind", 3: "Full House",
        4: "Flush", 5: "Straight", 6: "Three of a Kind",
        7: "Two Pair", 8: "Pair", 9: "High Card"
    }

    def __init__(self):
        self.flush_lookup: Dict[int, int] = {}
        self.unsuited_lookup: Dict[int, int] = {}
        self._calculate_flushes()
        self._calculate_multiples()

    def _get_lexographically_next_bit_sequence(self, bits: int) -> Generator[int, None, None]:
        """Генерирует следующий битовый шаблон той же длины и с тем же количеством единиц."""
        c = bits & -bits
        r = bits + c
        if r == 0:
            return
        yield (((r ^ bits) >> 2) // c) | r
        for nxt in self._get_lexographically_next_bit_sequence(((r ^ bits) >> 2) // c | r):
            yield nxt

    def _calculate_flushes(self):
        straight_flushes = [
            0b1111100000000, 0b0111110000000, 0b0011111000000,
            0b0001111100000, 0b0000111110000, 0b0000011111000,
            0b0000001111100, 0b0000000111110, 0b0000000011111,
            0b1000000001111  # Wheel
        ]
        normal_flushes = []
        start = (1 << 5) - 1
        normal_flushes.append(start)
        gen = self._get_lexographically_next_bit_sequence(start)
        try:
            while True:
                normal_flushes.append(next(gen))
        except StopIteration:
            pass

        # Straight Flushes
        rank = 1
        for bits in straight_flushes:
            prod = self._prime_product_from_rankbits(bits)
            self.flush_lookup[prod] = rank
            rank += 1

        # обычные флеши
        rank = LookupTable5Card.MAX_FULL_HOUSE + 1
        for bits in normal_flushes:
            prod = self._prime_product_from_rankbits(bits)
            self.flush_lookup[prod] = rank
            rank += 1

        # стриты и старшие карты
        self._calculate_straights_and_highcards(straight_flushes, normal_flushes)

    def _calculate_straights_and_highcards(self,
            straights_bits: List[int],
            highcards_bits: List[int]):
        # стриты
        rank = LookupTable5Card.MAX_FLUSH + 1
        for bits in straights_bits:
            prod = self._prime_product_from_rankbits(bits)
            self.unsuited_lookup[prod] = rank
            rank += 1
        # старшие карты
        rank = LookupTable5Card.MAX_PAIR + 1
        for bits in highcards_bits:
            prod = self._prime_product_from_rankbits(bits)
            self.unsuited_lookup[prod] = rank
            rank += 1

    def _prime_product_from_rankbits(self, bits: int) -> int:
        """Переводит 13-битную маску рангов в произведение соответствующих простых."""
        prod = 1
        for i in range(13):
            if bits & (1 << i):
                prod *= PRIMES[i]
        return prod

    def _calculate_multiples(self):
        backwards = list(INT_RANKS)[::-1]

        # Каре
        rank = LookupTable5Card.MAX_STRAIGHT_FLUSH + 1
        for quad in backwards:
            kickers = sorted([k for k in backwards if k != quad])
            for k in kickers:
                prod = PRIMES[quad]**4 * PRIMES[k]
                self.unsuited_lookup[prod] = rank
                rank += 1

        # Фулл-хаус
        rank = LookupTable5Card.MAX_FOUR_OF_A_KIND + 1
        for trip in backwards:
            pairs = sorted([p for p in backwards if p != trip])
            for p in pairs:
                prod = PRIMES[trip]**3 * PRIMES[p]**2
                self.unsuited_lookup[prod] = rank
                rank += 1

        # Сет
        rank = LookupTable5Card.MAX_STRAIGHT + 1
        for trip in backwards:
            kickers = [k for k in backwards if k != trip]
            combos = sorted(itertools.combinations(kickers, 2))
            for k1, k2 in combos:
                prod = PRIMES[trip]**3 * PRIMES[k1] * PRIMES[k2]
                self.unsuited_lookup[prod] = rank
                rank += 1

        # Две пары
        rank = LookupTable5Card.MAX_THREE_OF_A_KIND + 1
        pair_combos = sorted(itertools.combinations(backwards, 2))
        for p1, p2 in pair_combos:
            kickers = sorted([k for k in backwards if k not in (p1, p2)])
            for k in kickers:
                prod = PRIMES[p1]**2 * PRIMES[p2]**2 * PRIMES[k]
                self.unsuited_lookup[prod] = rank
                rank += 1

        # Пара
        rank = LookupTable5Card.MAX_TWO_PAIR + 1
        for p in backwards:
            kickers = [k for k in backwards if k != p]
            combos = sorted(itertools.combinations(kickers, 3))
            for k1, k2, k3 in combos:
                prod = PRIMES[p]**2 * PRIMES[k1] * PRIMES[k2] * PRIMES[k3]
                self.unsuited_lookup[prod] = rank
                rank += 1

class Evaluator5Card:
    """Пользовательский интерфейс к таблицам."""
    def __init__(self):
        self.table = LookupTable5Card()

    def evaluate(self, cards: List[int]) -> int:
        if len(cards) != 5:
            raise ValueError("Requires 5 cards.")
        valid = []
        for c in cards:
            if not isinstance(c, int) or c == INVALID_CARD or c <= 0:
                raise ValueError(f"Invalid card: {c}")
            valid.append(c)
        if len(set(valid)) != 5:
            raise ValueError("Duplicate cards found.")

        # проверка флеша
        suit_mask = valid[0] & valid[1] & valid[2] & valid[3] & valid[4] & 0xF000
        if suit_mask != 0:
            bits = 0
            for c in valid:
                bits |= (c >> 16)
            prod = self.table._prime_product_from_rankbits(bits)
            return self.table.flush_lookup.get(prod, self.table.WORST_RANK_5CARD)
        else:
            prod = 1
            for c in valid:
                prod *= Card.get_prime(c)
            return self.table.unsuited_lookup.get(prod, self.table.WORST_RANK_5CARD)

    def get_rank_class(self, r: int) -> int:
        """1–9 классы: 1=RF … 9=High Card."""
        if not isinstance(r, int) or r <= 0:
            return 9
        t = self.table
        if r <= t.MAX_STRAIGHT_FLUSH:
            return 1
        if r <= t.MAX_FOUR_OF_A_KIND:
            return 2
        if r <= t.MAX_FULL_HOUSE:
            return 3
        if r <= t.MAX_FLUSH:
            return 4
        if r <= t.MAX_STRAIGHT:
            return 5
        if r <= t.MAX_THREE_OF_A_KIND:
            return 6
        if r <= t.MAX_TWO_PAIR:
            return 7
        if r <= t.MAX_PAIR:
            return 8
        if r <= t.MAX_HIGH_CARD:
            return 9
        return 9

    def class_to_string(self, cls: int) -> str:
        return self.table.RANK_CLASS_TO_STRING.get(cls, "Unknown")

# Глобальный экземпляр
evaluator_5card_instance = Evaluator5Card()
