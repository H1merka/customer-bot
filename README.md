# customer-bot

A Telegram bot for piercing studio operations with booking, admin controls, and support chat bridging.

## Features
- Booking flow with service selection and basic medical questions
- PostgreSQL-backed persistence via SQLAlchemy
- Google Calendar integration with graceful degradation
- Admin commands for access control and support workflows
- Docker and docker-compose support

## Environment
Copy [.env.example](.env.example) to .env and fill in the required values.

## Run locally
1. Install dependencies with `uv sync`
2. Start the database with `docker compose up -d db`
3. Run the bot with `uv run python main.py`

## Tests
Run `uv run python -m unittest discover -s tests -v`

## Maintainers

[@H1merka](https://github.com/H1merka).

## Contributing

Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

## License

[MIT](LICENSE) © Morgenshtern Dmitrij
