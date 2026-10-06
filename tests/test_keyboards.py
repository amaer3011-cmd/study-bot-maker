from __future__ import annotations

from main import StudyBot


def test_all_keyboard_buttons_have_supported_color_styles() -> None:
    keyboards = [
        StudyBot.categories_keyboard(),
        StudyBot.template_nav_keyboard(1, 1),
        StudyBot.time_choice_keyboard(),
        StudyBot.duration_keyboard(),
        StudyBot.task_keyboard(True),
        StudyBot.final_keyboard(),
        StudyBot.timer_keyboard("https://t.me/example"),
        StudyBot.completed_tasks_keyboard(10),
        StudyBot.rating_keyboard(),
        StudyBot.admin_keyboard(),
        StudyBot.subscription_keyboard(("@study_channel",)),
        StudyBot.group_camp_keyboard(1),
    ]
    styles = {
        button.style
        for keyboard in keyboards
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert styles == {"primary", "success", "danger"}
    assert StudyBot.time_choice_keyboard().inline_keyboard[-1][-1].style == "danger"
    assert StudyBot.task_keyboard(True).inline_keyboard[0][0].style == "success"
