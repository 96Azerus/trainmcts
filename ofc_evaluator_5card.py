# ofc_evaluator_5card.py v1.2
"""
Оценка 5-карточной руки OFC + генерация таблиц поиска.
Исправлен метод evaluate для unsuited рук.
"""
import itertools
import traceback
import sys
import logging
from typing import Dict, List, Generator, Optional
# Убран неиспользуемый импорт Counter

# Импортируем Card и PRIMES из ofc_logic
try:
    from ofc_logic import Card, PRIMES, INT_RANKS, INVALID_CARD
except ImportError:
    # Заглушки для возможности анализа
    class Card:
        PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41]
        INT_RANKS = range(13)
        @staticmethod
        def get_prime(c): return 1
        @staticmethod
        def get_rank_int(c): return 0
        @staticmethod
        def prime_product_from_rankbits(rankbits): return 1
        @staticmethod
        def prime_product_from_hand(card_ints): return 1
        @staticmethod
        def to_str(c): return "??"
    PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41]
    INT_RANKS = range(13)
    INVALID_CARD = -1
    logging.error("Could not import from ofc_logic in ofc_evaluator_5card.py")

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)

# --- Класс LookupTable5Card ---
class LookupTable5Card:
    """Создает и хранит таблицы поиска для 5-карточных рук."""
    MAX_STRAIGHT_FLUSH: int = 10
    MAX_FOUR_OF_A_KIND: int = 166
    MAX_FULL_HOUSE: int = 322
    MAX_FLUSH: int = 1599
    MAX_STRAIGHT: int = 1609
    MAX_THREE_OF_A_KIND: int = 2467
    MAX_TWO_PAIR: int = 3325
    MAX_PAIR: int = 6185
    MAX_HIGH_CARD: int = 7462
    WORST_RANK_5CARD: int = MAX_HIGH_CARD + 1 # Ранг для ошибок

    RANK_CLASS_TO_STRING: Dict[int, str] = {
        1: "Straight Flush", 2: "Four of a Kind", 3: "Full House", 4: "Flush",
        5: "Straight", 6: "Three of a Kind", 7: "Two Pair", 8: "Pair", 9: "High Card"
    }

    def __init__(self):
        """Инициализирует и вычисляет таблицы поиска."""
        self.flush_lookup: Dict[int, int] = {}
        self.unsuited_lookup: Dict[int, int] = {}
        logger.info("Initializing 5-card lookup tables...")
        try:
            self._calculate_flushes()
            self._calculate_multiples()
            logger.info(f"5-card tables initialized. Flush: {len(self.flush_lookup)}, Unsuited: {len(self.unsuited_lookup)}")
        except Exception as e:
             logger.critical(f"Error during 5-card lookup table calculation: {e}", exc_info=True)
             raise

    def _calculate_flushes(self):
        """Вычисляет ранги для стрит-флешей и обычных флешей."""
        straight_flushes_rank_bits: List[int] = [
            0b1111100000000, 0b0111110000000, 0b0011111000000, 0b0001111100000,
            0b0000111110000, 0b0000011111000, 0b0000001111100, 0b0000000111110,
            0b0000000011111, 0b1000000001111, # Wheel
        ]
        all_flush_rank_bits: List[int] = []
        start_bits = (1 << 5) - 1
        all_flush_rank_bits.append(start_bits)
        gen = self._get_lexographically_next_bit_sequence(start_bits)
        try:
            while True: all_flush_rank_bits.append(next(gen))
        except StopIteration: pass
        except Exception as e: logger.error(f"Error generating bit sequence: {e}", exc_info=True)

        straight_flush_set = set(straight_flushes_rank_bits)
        normal_flush_rank_bits = sorted([rb for rb in all_flush_rank_bits if rb not in straight_flush_set], reverse=True)

        rank = 1
        for sf_bits in straight_flushes_rank_bits:
            prime_product = self._prime_product_from_rankbits(sf_bits)
            self.flush_lookup[prime_product] = rank; rank += 1
        if rank -1 != self.MAX_STRAIGHT_FLUSH: logger.error(f"SF ranks mismatch: {rank-1} vs {self.MAX_STRAIGHT_FLUSH}")

        rank = self.MAX_FULL_HOUSE + 1
        for f_bits in normal_flush_rank_bits:
            prime_product = self._prime_product_from_rankbits(f_bits)
            self.flush_lookup[prime_product] = rank; rank += 1
        if rank -1 != self.MAX_FLUSH: logger.warning(f"Flush ranks mismatch: {rank-1} vs {self.MAX_FLUSH}")

        self._calculate_straights_and_highcards(straight_flushes_rank_bits, normal_flush_rank_bits)

    def _calculate_straights_and_highcards(self, straights_rank_bits: List[int], highcards_rank_bits: List[int]):
        """Вычисляет ранги для стритов и старших карт (не флеш)."""
        rank = self.MAX_FLUSH + 1
        for s_bits in straights_rank_bits:
            prime_product = self._prime_product_from_rankbits(s_bits)
            self.unsuited_lookup[prime_product] = rank; rank += 1
        if rank -1 != self.MAX_STRAIGHT: logger.warning(f"Straight ranks mismatch: {rank-1} vs {self.MAX_STRAIGHT}")

        rank = self.MAX_PAIR + 1
        for h_bits in highcards_rank_bits:
            prime_product = self._prime_product_from_rankbits(h_bits)
            self.unsuited_lookup[prime_product] = rank; rank += 1
        if rank -1 != self.MAX_HIGH_CARD: logger.warning(f"High card ranks mismatch: {rank-1} vs {self.MAX_HIGH_CARD}")

    def _calculate_multiples(self):
        """Вычисляет ранги для Каре, Фулл-хаусов, Сетов, Двух пар и Пар."""
        backwards_ranks = range(len(INT_RANKS) - 1, -1, -1)

        # Каре
        rank = self.MAX_STRAIGHT_FLUSH + 1
        for quad_idx in backwards_ranks:
            kickers = sorted([k for k in backwards_ranks if k != quad_idx], reverse=True)
            for kick_idx in kickers:
                prod = PRIMES[quad_idx]**4 * PRIMES[kick_idx]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_FOUR_OF_A_KIND: logger.warning(f"4oak ranks mismatch: {rank-1} vs {self.MAX_FOUR_OF_A_KIND}")

        # Фулл-хаус
        rank = self.MAX_FOUR_OF_A_KIND + 1
        for trip_idx in backwards_ranks:
            pairs = sorted([p for p in backwards_ranks if p != trip_idx], reverse=True)
            for pair_idx in pairs:
                prod = PRIMES[trip_idx]**3 * PRIMES[pair_idx]**2
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_FULL_HOUSE: logger.warning(f"FH ranks mismatch: {rank-1} vs {self.MAX_FULL_HOUSE}")

        # Сет
        rank = self.MAX_STRAIGHT + 1
        for trip_idx in backwards_ranks:
            kickers = [k for k in backwards_ranks if k != trip_idx]
            kicker_combos = sorted(itertools.combinations(kickers, 2), reverse=True)
            for k1, k2 in kicker_combos:
                prod = PRIMES[trip_idx]**3 * PRIMES[k1] * PRIMES[k2]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_THREE_OF_A_KIND: logger.warning(f"3oak ranks mismatch: {rank-1} vs {self.MAX_THREE_OF_A_KIND}")

        # Две пары
        rank = self.MAX_THREE_OF_A_KIND + 1
        pair_combos = sorted(itertools.combinations(backwards_ranks, 2), reverse=True)
        for p1, p2 in pair_combos:
            kickers = sorted([k for k in backwards_ranks if k != p1 and k != p2], reverse=True)
            for kick_idx in kickers:
                prod = PRIMES[p1]**2 * PRIMES[p2]**2 * PRIMES[kick_idx]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_TWO_PAIR: logger.warning(f"2pair ranks mismatch: {rank-1} vs {self.MAX_TWO_PAIR}")

        # Пара
        rank = self.MAX_TWO_PAIR + 1
        for pair_idx in backwards_ranks:
            kickers = [k for k in backwards_ranks if k != pair_idx]
            kicker_combos = sorted(itertools.combinations(kickers, 3), reverse=True)
            for k1, k2, k3 in kicker_combos:
                prod = PRIMES[pair_idx]**2 * PRIMES[k1] * PRIMES[k2] * PRIMES[k3]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_PAIR: logger.warning(f"Pair ranks mismatch: {rank-1} vs {self.MAX_PAIR}")

    def _get_lexographically_next_bit_sequence(self, bits: int) -> Generator[int, None, None]:
        """Генератор следующей лексикографической перестановки битов (Gosper's Hack)."""
        next_val = bits
        while True:
            if next_val == 0: break
            try:
                rightmost_one = next_val & -next_val; next_higher_one_bit = next_val + rightmost_one
                rightmost_block_of_ones = next_val ^ next_higher_one_bit
                rightmost_block_shifted = (rightmost_block_of_ones // rightmost_one) >> 2
                next_val = next_higher_one_bit | rightmost_block_shifted
            except Exception as e_gosper: logger.error(f"Error in Gosper's Hack: {e_gosper}", exc_info=True); break
            if next_val >= (1 << 13): break
            yield next_val

    def _prime_product_from_rankbits(self, rankbits: int) -> int:
        """Вычисляет произведение простых чисел для рангов из битовой маски."""
        product = 1
        for i in INT_RANKS:
            if rankbits & (1 << i):
                try: product *= PRIMES[i]
                except IndexError: logger.warning(f"Invalid rank index {i} in prime product calculation.")
        return product

# --- Класс Evaluator5Card ---
class Evaluator5Card:
    """Оценивает 5-карточные руки, используя таблицы поиска."""
    def __init__(self):
        """Инициализирует эвалуатор, загружая таблицы."""
        self.table = LookupTable5Card()

    def evaluate(self, cards: List[int]) -> int:
        """Оценивает 5-карточную руку."""
        if len(cards) != 5: raise ValueError("Requires 5 cards.")
        valid_cards: List[int] = []
        for c in cards:
             if not isinstance(c, int) or c == INVALID_CARD or c <= 0: raise ValueError(f"Invalid card: {c}")
             valid_cards.append(c)
        if len(valid_cards) != len(set(valid_cards)): raise ValueError("Duplicate cards found.")

        suit_mask = valid_cards[0] & valid_cards[1] & valid_cards[2] & valid_cards[3] & valid_cards[4] & 0xF000
        if suit_mask != 0: # Флеш или Стрит-флеш
            rank_bitmask = (valid_cards[0] | valid_cards[1] | valid_cards[2] | valid_cards[3] | valid_cards[4]) >> 16
            prime_product = self.table._prime_product_from_rankbits(rank_bitmask)
            rank = self.table.flush_lookup.get(prime_product)
            if rank is None: logger.warning(f"Flush prime product {prime_product} not found."); return self.table.WORST_RANK_5CARD
            return rank
        else: # Не флеш
            # --- ИСПРАВЛЕНО: Вычисляем prime_product как произведение простых чисел всех 5 карт ---
            prime_product = 1
            try:
                for card_int in valid_cards:
                    prime_product *= Card.get_prime(card_int)
            except Exception as e:
                logger.error(f"Error calculating prime product for unsuited hand: {e}")
                return self.table.WORST_RANK_5CARD

            rank = self.table.unsuited_lookup.get(prime_product)
            if rank is None: logger.warning(f"Unsuited prime product {prime_product} not found."); return self.table.WORST_RANK_5CARD
            return rank

    def get_rank_class(self, hand_rank: int) -> int:
        """Возвращает класс руки (1-9) по её рангу."""
        if not isinstance(hand_rank, int) or hand_rank <= 0: return 9
        if hand_rank <= self.table.MAX_STRAIGHT_FLUSH: return 1
        elif hand_rank <= self.table.MAX_FOUR_OF_A_KIND: return 2
        elif hand_rank <= self.table.MAX_FULL_HOUSE: return 3
        elif hand_rank <= self.table.MAX_FLUSH: return 4
        elif hand_rank <= self.table.MAX_STRAIGHT: return 5
        elif hand_rank <= self.table.MAX_THREE_OF_A_KIND: return 6
        elif hand_rank <= self.table.MAX_TWO_PAIR: return 7
        elif hand_rank <= self.table.MAX_PAIR: return 8
        elif hand_rank <= self.table.MAX_HIGH_CARD: return 9
        else: logger.warning(f"Invalid hand rank {hand_rank} in get_rank_class."); return 9

    def class_to_string(self, class_int: int) -> str:
        """Преобразует целочисленный класс руки в строку."""
        return self.table.RANK_CLASS_TO_STRING.get(class_int, "Unknown")

# Создаем глобальный экземпляр для использования другими модулями
evaluator_5card_instance = Evaluator5Card()
