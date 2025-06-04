import pytest
import random
from unittest.mock import patch

# try:
from ofc_logic import Card, PlayerBoard, Deck, SUIT_CHAR_TO_INT, INT_RANK_TO_CHAR, INT_SUIT_TO_CHAR # Changed import
from mcts_node import MCTSNode
# except ImportError:
#     pytest.skip("Skipping uncertainty tests due to missing core imports", allow_module_level=True)

# Define suit constants based on how they are in ofc_logic.py
SUIT_SPADES = SUIT_CHAR_TO_INT['s']
SUIT_HEARTS = SUIT_CHAR_TO_INT['h']
SUIT_DIAMONDS = SUIT_CHAR_TO_INT['d']
SUIT_CLUBS = SUIT_CHAR_TO_INT['c']

def hand_to_int(card_strs: list) -> list: # Helper
    return [Card.from_str(s) for s in card_strs if s]

class TestUncertaintyHandling:
    def test_count_specific_outs(self):
        # 5 spades + 5 hearts = 10 cards in this specific deck portion being evaluated
        deck_cards = {
            Card.from_str('As'), Card.from_str('Ks'), Card.from_str('Qs'), Card.from_str('Js'), Card.from_str('Ts'),
            Card.from_str('Ah'), Card.from_str('Kh'), Card.from_str('Qh'), Card.from_str('Jh'), Card.from_str('Th')
        }
        check_spade = lambda card_int: Card.get_suit_int(card_int) == SUIT_SPADES
        visible_spades = sum(1 for card in deck_cards if check_spade(card)) # Should be 5

        # Scenario 1: No unknowns, original_deck_size is the actual size of deck_cards
        # This tests the case where the 'deck' passed to _count_specific_outs is the full conceptual remaining deck.
        eff_outs_1 = MCTSNode._count_specific_outs(deck_cards, check_spade, 0, len(deck_cards))
        assert abs(eff_outs_1 - float(visible_spades)) < 1e-9, "Scenario 1 failed"

        # Scenario 2: Some unknowns, original_deck_size reflects a larger conceptual deck.
        # deck_cards represents the *known* part of a larger conceptual deck.
        # original_deck_size is the size of that conceptual deck before unknowns are 'subtracted' for probability.
        conceptual_deck_size = 20
        num_unknown_removed = 5
        # visible_spades are still 5 as they are seen in deck_cards
        expected_eff_outs_2 = float(visible_spades) * (conceptual_deck_size - num_unknown_removed) / float(conceptual_deck_size)
        eff_outs_2 = MCTSNode._count_specific_outs(deck_cards, check_spade, num_unknown_removed, conceptual_deck_size)
        assert abs(eff_outs_2 - expected_eff_outs_2) < 1e-9, f"Scenario 2 failed: expected {expected_eff_outs_2}, got {eff_outs_2}"

        # Scenario 3: High unknowns
        num_unknown_removed_high = 15
        expected_eff_outs_3 = float(visible_spades) * (conceptual_deck_size - num_unknown_removed_high) / float(conceptual_deck_size)
        eff_outs_3 = MCTSNode._count_specific_outs(deck_cards, check_spade, num_unknown_removed_high, conceptual_deck_size)
        assert abs(eff_outs_3 - expected_eff_outs_3) < 1e-9, f"Scenario 3 failed: expected {expected_eff_outs_3}, got {eff_outs_3}"

        # Scenario 4: Unknowns equal original conceptual size
        eff_outs_4 = MCTSNode._count_specific_outs(deck_cards, check_spade, conceptual_deck_size, conceptual_deck_size)
        assert abs(eff_outs_4 - 0.0) < 1e-9, "Scenario 4 failed"

        # Scenario 5: No visible outs in the 'deck_cards' set passed
        check_diamond = lambda card_int: Card.get_suit_int(card_int) == SUIT_DIAMONDS
        eff_outs_5 = MCTSNode._count_specific_outs(deck_cards, check_diamond, 2, conceptual_deck_size)
        assert abs(eff_outs_5 - 0.0) < 1e-9, "Scenario 5 failed"

        # Scenario 6: original_deck_size is zero
        eff_outs_6 = MCTSNode._count_specific_outs(deck_cards, check_spade, 2, 0)
        assert abs(eff_outs_6 - 0.0) < 1e-9, "Scenario 6 failed"

        # Scenario 7: original_deck_size less than num_unknown_removed
        eff_outs_7 = MCTSNode._count_specific_outs(deck_cards, check_spade, 5, 2)
        assert abs(eff_outs_7 - 0.0) < 1e-9, "Scenario 7 failed"

        # Scenario 8: All cards are unknown
        eff_outs_8 = MCTSNode._count_specific_outs(deck_cards, check_spade, conceptual_deck_size, conceptual_deck_size)
        assert abs(eff_outs_8 - 0.0) < 1e-9, "Scenario 8 failed"


    def test_get_dynamic_weights_uncertainty(self):
        cards_on_board_mid_game = 7
        weights_low_unk = MCTSNode._get_dynamic_weights(cards_placed_on_board=cards_on_board_mid_game, num_unknown_removed=0)
        weights_high_unk = MCTSNode._get_dynamic_weights(cards_placed_on_board=cards_on_board_mid_game, num_unknown_removed=4) # num_unknown_removed > 2 triggers reduction

        assert weights_high_unk['draw_potential_multiplier'] < weights_low_unk['draw_potential_multiplier']
        assert weights_high_unk['flush_draw_score_per_out'] < weights_low_unk['flush_draw_score_per_out']
        assert weights_high_unk['open_ended_draw_score_per_out'] < weights_low_unk['open_ended_draw_score_per_out']
        assert weights_high_unk['gutshot_draw_score_per_out'] < weights_low_unk['gutshot_draw_score_per_out']

        # Test a different game stage
        cards_on_board_early_game = 3
        weights_early_low_unk = MCTSNode._get_dynamic_weights(cards_placed_on_board=cards_on_board_early_game, num_unknown_removed=0)
        weights_early_high_unk = MCTSNode._get_dynamic_weights(cards_placed_on_board=cards_on_board_early_game, num_unknown_removed=3)

        assert weights_early_high_unk['draw_potential_multiplier'] < weights_early_low_unk['draw_potential_multiplier']


    def test_estimate_row_potential_uncertainty(self):
        cards_placed_for_dw = 8 # For _get_dynamic_weights call, represents cards on ENTIRE board

        # 4-card hand: 4 spades (As, Ks, Qs, 2s). This is a made flush, but also draws to SF with Js or Ts.
        current_row_cards = hand_to_int(['As', 'Ks', 'Qs', '2s'])

        # Deck contains outs for straight flush (Js for A-K-Q-J-T SF, or Ts for A-K-Q-J-T SF)
        # and other cards.
        deck_node_remaining = {
            Card.from_str('Js'), Card.from_str('Ts'), # Spade outs for SF
            Card.from_str('Ad'), Card.from_str('Kd'), # Pair outs for A, K
            Card.from_str('2h'), # Pair out for 2
            # Add some more non-heart, non-spade, non-relevant cards
            Card.from_str('3c'), Card.from_str('4d'), Card.from_str('5c'), Card.from_str('6d'), Card.from_str('8c'),
            Card.from_str('9d')
        }
        # Ensure no cards from current_row_cards are in deck_node_remaining
        for card_in_hand in current_row_cards:
            deck_node_remaining.discard(card_in_hand)

        original_deck_size_in_node = len(deck_node_remaining) # This is the size of the "known" part of the deck for the node

        # Low uncertainty (0 unknown cards removed from the original full deck concept)
        weights_low_unk = MCTSNode._get_dynamic_weights(cards_placed_for_dw, 0)
        potential_low_unk = MCTSNode._estimate_row_potential(current_row_cards, deck_node_remaining, 0, original_deck_size_in_node, weights_low_unk)

        # High uncertainty (e.g., 5 unknown cards have been removed from original_deck_size_in_node)
        # This means actual outs in deck_node_remaining are less likely to be truly available.
        num_high_unknowns = min(5, original_deck_size_in_node -1 if original_deck_size_in_node > 0 else 0)

        weights_high_unk = MCTSNode._get_dynamic_weights(cards_placed_for_dw, num_high_unknowns)
        potential_high_unk = MCTSNode._estimate_row_potential(current_row_cards, deck_node_remaining, num_high_unknowns, original_deck_size_in_node, weights_high_unk)

        print(f"Uncertainty Test - Potential Low Unk: {potential_low_unk}, Potential High Unk: {potential_high_unk}, Num high unknowns: {num_high_unknowns}, Orig deck size: {original_deck_size_in_node}")
        assert potential_high_unk < potential_low_unk, \
            f"Potential with high uncertainty ({potential_high_unk}) should be lower than with low uncertainty ({potential_low_unk})"
        assert potential_low_unk > 0, "Potential with low uncertainty should be greater than 0 for a draw"

    def test_calculate_heuristic_score_uncertainty(self):
        board = PlayerBoard()
        # Setup a board where a draw is prominent
        board.add_card(Card.from_str('As'), 'bottom', 0)
        board.add_card(Card.from_str('Ks'), 'bottom', 1)
        board.add_card(Card.from_str('Qs'), 'bottom', 2)
        board.add_card(Card.from_str('Js'), 'bottom', 3) # 4 spades on bottom
        cards_placed_on_board = board.get_total_cards() # Should be 4

        # deck_snapshot is MCTSNode.remaining_deck: cards not on board.
        deck_snapshot = {Card.from_str('Ts')} # The spade out for bottom
        for i in range(20): # Add 20 non-spade cards
            suit = SUIT_HEARTS if i % 2 == 0 else SUIT_CLUBS
            rank_char = INT_RANK_TO_CHAR.get(i % 13, '2') # CORRECTED ACCESS
            if rank_char == 'T' and suit == SUIT_SPADES: continue
            deck_snapshot.add(Card.from_str(f"{rank_char}{INT_SUIT_TO_CHAR[suit]}")) # CORRECTED ACCESS & USAGE

        original_deck_size_for_snapshot = len(deck_snapshot)

        # Low uncertainty
        score_low_unk = MCTSNode._calculate_heuristic_score_v2(
            board, deck_snapshot, False, 0, original_deck_size_for_snapshot
        )

        # High uncertainty
        num_high_unknowns = 10
        score_high_unk = MCTSNode._calculate_heuristic_score_v2(
            board, deck_snapshot, False, num_high_unknowns, original_deck_size_for_snapshot
        )

        assert score_high_unk < score_low_unk, "Heuristic score with high uncertainty should be lower"
        # Base royalty for A K Q J (no pair) is 0. Draw potential should make it positive.
        # The exact value depends on dynamic weights, but it should be positive.
        assert score_low_unk > 0, "Base score with low uncertainty should be positive due to draw potential"
