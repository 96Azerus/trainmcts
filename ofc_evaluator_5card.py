# ofc_evaluator_5card.py v1.9 (DEBUG VERSION)
# ИЗМЕНЕНО: Уровень логгера по умолчанию на INFO
"""
Оценка 5-карточной руки OFC + генерация таблиц поиска.
"""
import itertools
# import traceback # Закомментировано, так как не используется напрямую
# import sys # Закомментировано, так как не используется напрямую
import logging
from typing import Dict, List, Generator, Optional, Tuple # Добавил Tuple
from collections import Counter

try:
    from ofc_logic import Card, PRIMES, INT_RANKS, INVALID_CARD, card_to_str
except ImportError:
    class Card: # type: ignore
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
    PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41] # type: ignore
    INT_RANKS = range(13) # type: ignore
    INVALID_CARD = -1 # type: ignore
    def card_to_str(c): return Card.to_str(c) # type: ignore
    logging.error("Could not import from ofc_logic in ofc_evaluator_5card.py")

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.INFO) # ИЗМЕНЕНО НА INFO
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# ... (остальной код файла ofc_evaluator_5card.py без изменений) ...
class LookupTable5Card:
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
        1: "Straight Flush", 2: "Four of a Kind", 3: "Full House", 4: "Flush",
        5: "Straight", 6: "Three of a Kind", 7: "Two Pair", 8: "Pair", 9: "High Card"
    }
    def __init__(self):
        self.flush_lookup: Dict[int, int] = {}
        self.unsuited_lookup: Dict[int, int] = {}
        logger.info("Initializing 5-card lookup tables...")
        try:
            self._calculate_flushes()
            self._calculate_multiples()
            logger.info(f"5-card tables initialized. Flush: {len(self.flush_lookup)}, Unsuited: {len(self.unsuited_lookup)}")
            rf_bits = 0b1111100000000
            rf_prime = self._prime_product_from_rankbits(rf_bits)
            if rf_prime not in self.flush_lookup:
                 logger.error(f"FATAL: Royal Flush prime product {rf_prime} not found in generated flush_lookup!")
            else:
                 logger.debug(f"Royal Flush prime product {rf_prime} found with rank {self.flush_lookup[rf_prime]}.")
        except Exception as e:
             logger.critical(f"Error during 5-card lookup table calculation: {e}", exc_info=True)
             raise
    def _calculate_flushes(self):
        straight_flushes_rank_bits: List[int] = [
            0b1111100000000, 0b0111110000000, 0b0011111000000, 0b0001111100000,
            0b0000111110000, 0b0000011111000, 0b0000001111100, 0b0000000111110,
            0b0000000011111, 0b1000000001111,
        ]
        all_flush_rank_bits: List[int] = []
        for combo_indices in itertools.combinations(INT_RANKS, 5):
            bits = 0
            for index in combo_indices: bits |= (1 << index)
            all_flush_rank_bits.append(bits)
        straight_flush_set = set(straight_flushes_rank_bits)
        normal_flush_rank_bits = sorted([rb for rb in all_flush_rank_bits if rb not in straight_flush_set], reverse=True)
        rank = 1
        for sf_bits in straight_flushes_rank_bits:
            prime_product = self._prime_product_from_rankbits(sf_bits)
            if prime_product == 0: logger.error(f"Zero prime product for SF bits {bin(sf_bits)}!"); continue
            self.flush_lookup[prime_product] = rank
            if rank == 1: logger.debug(f"Generated RF entry: prime={prime_product}, rank={rank}")
            rank += 1
        if rank -1 != self.MAX_STRAIGHT_FLUSH: logger.error(f"SF ranks mismatch: Expected {self.MAX_STRAIGHT_FLUSH}, got {rank-1}")
        rank = self.MAX_FULL_HOUSE + 1
        for f_bits in normal_flush_rank_bits:
            prime_product = self._prime_product_from_rankbits(f_bits)
            if prime_product == 0: logger.error(f"Zero prime product for Flush bits {bin(f_bits)}!"); continue
            self.flush_lookup[prime_product] = rank; rank += 1
        if rank -1 != self.MAX_FLUSH: logger.warning(f"Flush ranks mismatch: Expected {self.MAX_FLUSH}, got {rank-1}")
        self._calculate_straights_and_highcards(straight_flushes_rank_bits, normal_flush_rank_bits)
    def _calculate_straights_and_highcards(self, straights_rank_bits: List[int], highcards_rank_bits: List[int]):
        rank = self.MAX_FLUSH + 1
        for s_bits in straights_rank_bits:
            prime_product = self._prime_product_from_rankbits(s_bits)
            if prime_product == 0: logger.error(f"Zero prime product for Straight bits {bin(s_bits)}!"); continue
            self.unsuited_lookup[prime_product] = rank; rank += 1
        if rank -1 != self.MAX_STRAIGHT: logger.warning(f"Straight ranks mismatch: Expected {self.MAX_STRAIGHT}, got {rank-1}")
        rank = self.MAX_PAIR + 1
        for h_bits in highcards_rank_bits:
            prime_product = self._prime_product_from_rankbits(h_bits)
            if prime_product == 0: logger.error(f"Zero prime product for High Card bits {bin(h_bits)}!"); continue
            self.unsuited_lookup[prime_product] = rank; rank += 1
        if rank -1 != self.MAX_HIGH_CARD: logger.warning(f"High card ranks mismatch: Expected {self.MAX_HIGH_CARD}, got {rank-1}")
    def _calculate_multiples(self):
        backwards_ranks = range(len(INT_RANKS) - 1, -1, -1)
        rank = self.MAX_STRAIGHT_FLUSH + 1
        for quad_idx in backwards_ranks:
            kickers = sorted([k for k in backwards_ranks if k != quad_idx], reverse=True)
            for kick_idx in kickers:
                prod = PRIMES[quad_idx]**4 * PRIMES[kick_idx]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_FOUR_OF_A_KIND: logger.warning(f"4oak ranks mismatch: Expected {self.MAX_FOUR_OF_A_KIND}, got {rank-1}")
        rank = self.MAX_FOUR_OF_A_KIND + 1
        for trip_idx in backwards_ranks:
            pairs = sorted([p for p in backwards_ranks if p != trip_idx], reverse=True)
            for pair_idx in pairs:
                prod = PRIMES[trip_idx]**3 * PRIMES[pair_idx]**2
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_FULL_HOUSE: logger.warning(f"FH ranks mismatch: Expected {self.MAX_FULL_HOUSE}, got {rank-1}")
        rank = self.MAX_STRAIGHT + 1
        for trip_idx in backwards_ranks:
            kickers = [k for k in backwards_ranks if k != trip_idx]
            kicker_combos = sorted(itertools.combinations(kickers, 2), reverse=True)
            for k1, k2 in kicker_combos:
                prod = PRIMES[trip_idx]**3 * PRIMES[k1] * PRIMES[k2]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_THREE_OF_A_KIND: logger.warning(f"3oak ranks mismatch: Expected {self.MAX_THREE_OF_A_KIND}, got {rank-1}")
        rank = self.MAX_THREE_OF_A_KIND + 1
        pair_combos = sorted(itertools.combinations(backwards_ranks, 2), reverse=True)
        for p1, p2 in pair_combos:
            kickers = sorted([k for k in backwards_ranks if k != p1 and k != p2], reverse=True)
            for kick_idx in kickers:
                prod = PRIMES[p1]**2 * PRIMES[p2]**2 * PRIMES[kick_idx]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_TWO_PAIR: logger.warning(f"2pair ranks mismatch: Expected {self.MAX_TWO_PAIR}, got {rank-1}")
        rank = self.MAX_TWO_PAIR + 1
        for pair_idx in backwards_ranks:
            kickers = [k for k in backwards_ranks if k != pair_idx]
            kicker_combos = sorted(itertools.combinations(kickers, 3), reverse=True)
            for k1, k2, k3 in kicker_combos:
                prod = PRIMES[pair_idx]**2 * PRIMES[k1] * PRIMES[k2] * PRIMES[k3]
                self.unsuited_lookup[prod] = rank; rank += 1
        if rank -1 != self.MAX_PAIR: logger.warning(f"Pair ranks mismatch: Expected {self.MAX_PAIR}, got {rank-1}")
    def _prime_product_from_rankbits(self, rankbits: int) -> int:
        product = 1
        for i in INT_RANKS:
            if rankbits & (1 << i):
                try: product *= PRIMES[i]
                except IndexError: logger.warning(f"Invalid rank index {i} for bits {bin(rankbits)}."); return 0
        return product
class Evaluator5Card:
    def __init__(self): self.table = LookupTable5Card()
    def evaluate(self, cards: List[int]) -> int:
        hand_str_log = [card_to_str(c) for c in cards]
        logger.debug(f"Evaluating 5-card hand: {hand_str_log} (ints: {cards})")
        if len(cards) != 5:
            logger.warning(f"Evaluator5Card.evaluate requires 5 cards, got {len(cards)}")
            return self.table.WORST_RANK_5CARD
        valid_cards: List[int] = []
        try:
            for c_int in cards:
                 if not isinstance(c_int, int) or c_int == INVALID_CARD or c_int <= 0: raise ValueError(f"Invalid card integer: {c_int}")
                 valid_cards.append(c_int)
            if len(valid_cards) != len(set(valid_cards)): raise ValueError(f"Duplicate cards found: {hand_str_log}")
        except ValueError as e:
            logger.warning(f"Invalid input for 5-card evaluation: {e}")
            return self.table.WORST_RANK_5CARD
        ranks_extracted = [Card.get_rank_int(card_int) for card_int in valid_cards]
        logger.debug(f"  Extracted ranks: {ranks_extracted}")
        suit_mask = valid_cards[0] & valid_cards[1] & valid_cards[2] & valid_cards[3] & valid_cards[4] & 0xF000
        if suit_mask != 0:
            logger.debug(f"Hand {hand_str_log} detected as Flush/SF (suit_mask={suit_mask})")
            rank_bitmask = 0
            for rank_int_val in ranks_extracted: rank_bitmask |= (1 << rank_int_val)
            logger.debug(f"Rank bitmask: {bin(rank_bitmask)}")
            prime_product = self.table._prime_product_from_rankbits(rank_bitmask)
            logger.debug(f"Calculated prime product (flush): {prime_product}")
            if prime_product == 0: logger.error(f"Zero prime product for flush hand: {hand_str_log}"); return self.table.WORST_RANK_5CARD
            rank = self.table.flush_lookup.get(prime_product)
            logger.debug(f"Lookup result in flush_lookup for {prime_product}: {rank}")
            if rank is None:
                rf_bits = 0b1111100000000
                if rank_bitmask == rf_bits: logger.error(f"FATAL: RF prime {prime_product} (bits {bin(rank_bitmask)}) not found for hand {hand_str_log}!")
                else: logger.warning(f"Flush prime {prime_product} not found for hand {hand_str_log} (bitmask {bin(rank_bitmask)})")
                return self.table.WORST_RANK_5CARD
            logger.debug(f"Returning rank (flush): {rank}"); return rank
        else:
            logger.debug(f"Hand {hand_str_log} detected as Unsuited")
            prime_product = 1
            try:
                rank_counts = Counter(ranks_extracted)
                logger.debug(f"Rank counts: {rank_counts}")
                for rank_index, count in rank_counts.items(): prime_product *= PRIMES[rank_index] ** count
                logger.debug(f"Calculated prime product (unsuited): {prime_product}")
            except Exception as e:
                logger.error(f"Error calculating prime product for unsuited hand {hand_str_log}: {e}")
                return self.table.WORST_RANK_5CARD
            rank = self.table.unsuited_lookup.get(prime_product)
            logger.debug(f"Lookup result in unsuited_lookup for {prime_product}: {rank}")
            if rank is None:
                logger.warning(f"Unsuited prime product {prime_product} not found for hand {hand_str_log} (ranks: {ranks_extracted})")
                return self.table.WORST_RANK_5CARD
            logger.debug(f"Returning rank (unsuited): {rank}"); return rank
    def get_rank_class(self, hand_rank: int) -> int:
        if not isinstance(hand_rank, int) or hand_rank <= 0 or hand_rank >= self.table.WORST_RANK_5CARD:
            logger.debug(f"get_rank_class received invalid rank: {hand_rank}, returning 9")
            return 9
        if hand_rank <= self.table.MAX_STRAIGHT_FLUSH: return 1
        elif hand_rank <= self.table.MAX_FOUR_OF_A_KIND: return 2
        elif hand_rank <= self.table.MAX_FULL_HOUSE: return 3
        elif hand_rank <= self.table.MAX_FLUSH: return 4
        elif hand_rank <= self.table.MAX_STRAIGHT: return 5
        elif hand_rank <= self.table.MAX_THREE_OF_A_KIND: return 6
        elif hand_rank <= self.table.MAX_TWO_PAIR: return 7
        elif hand_rank <= self.table.MAX_PAIR: return 8
        elif hand_rank <= self.table.MAX_HIGH_CARD: return 9
        else: logger.warning(f"Unexpected hand rank {hand_rank} in get_rank_class."); return 9
    def class_to_string(self, class_int: int) -> str:
        return self.table.RANK_CLASS_TO_STRING.get(class_int, "Unknown")
try:
    evaluator_5card_instance = Evaluator5Card()
except Exception as e_global:
    logger.critical(f"Failed to create global Evaluator5Card instance: {e_global}", exc_info=True)
    class MockEvaluator5CardGlobal: # type: ignore
        MAX_HIGH_CARD = 7462; WORST_RANK_5CARD = MAX_HIGH_CARD + 1
        def evaluate(self, cards): return self.WORST_RANK_5CARD
        def get_rank_class(self, rank): return 9
        def class_to_string(self, r_class): return "Error"
        table = MockEvaluator5CardGlobal() # type: ignore
    evaluator_5card_instance = MockEvaluator5CardGlobal() # type: ignore
