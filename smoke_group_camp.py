from config import Settings
from main import StudyBot, build_application

settings = Settings("123456:TEST", 3000, ":memory:", 20, 180, 720, 0)
bot = StudyBot(settings)
application = build_application(bot)
commands = []
for handler in application.handlers[0]:
    if getattr(handler, "callback", None) == bot.camp:
        commands.append(tuple(getattr(handler, "commands", ())))
assert any(set(item) == {"camp", "group_session"} for item in commands)
assert len(commands) >= 2  # command handler + channel-post handler
print({"status": "ok", "camp_handlers": commands})
