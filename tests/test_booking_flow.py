import unittest

from handlers.client import build_slot_menu, get_booking_next_state


class BookingFlowTests(unittest.TestCase):
    def test_piercing_service_requires_medical_questions(self) -> None:
        self.assertEqual(get_booking_next_state("Прокол", 25), "medical_question_1")

    def test_non_piercing_services_skip_medical_flow(self) -> None:
        self.assertEqual(get_booking_next_state("Апсайз", 25), "select_slot")

    def test_slot_menu_contains_multiple_slots(self) -> None:
        menu = build_slot_menu()
        self.assertGreater(len(menu.inline_keyboard), 0)


if __name__ == "__main__":
    unittest.main()
