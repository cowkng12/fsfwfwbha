# Telegram NFT Research Mini App

Telegram Mini App для подбора выгодных Telegram NFT-подарков по NFT, фону и модели. Приложение не является маркетплейсом: оно получает листинги из MRKT, сравнивает цены только внутри одной коллекции и показывает самые выгодные варианты.

## Структура

- `backend/app` - FastAPI API, SQLite-хранилище, MRKT-клиент, фоновый ресерч каждые 3 минуты.
- `backend/app/catalogs` - справочники NFT, фонов и моделей.
- `frontend/src` - Telegram Mini App интерфейс на React/Vite.
- `.env.example` - шаблон переменных окружения. Реальные токены храните только в `.env`.

## Запуск

1. Установить backend зависимости: `pip install -r backend/requirements.txt`.
2. Скопировать `.env.example` в `.env` и заполнить Telegram/MRKT переменные.
3. Запустить backend: `python -m uvicorn app.main:app --reload --app-dir backend`.
4. Установить frontend зависимости: `npm install`.
5. Запустить frontend: `npm run dev`.

## Render

Проект готов к деплою одним Render Web Service через Blueprint `render.yaml`. FastAPI отдает API и собранный frontend из `frontend/dist`.

1. Подключите GitHub репозиторий к Render.
2. Создайте Blueprint из `render.yaml`.
3. В Render Dashboard заполните secret env vars: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION`, `MRKT_AUTH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`, `CRON_SECRET`.
4. После первого деплоя укажите домен сервиса в `CORS_ORIGINS`, если Render выдаст другое имя.

Для ответа бота на `/start` задайте `PUBLIC_BASE_URL` равным публичному URL Render-сервиса. Backend сам установит Telegram webhook при старте.

Для whitelist людей задайте в Render env `TELEGRAM_ALLOWED_USER_IDS` через запятую, например `123456789,987654321`. Можно также использовать `TELEGRAM_ALLOWED_CHAT_IDS`; для личного чата Telegram `chat_id` обычно равен `user_id`. Если оба значения пустые, бот и Mini App открыты всем. Пользователь вне списка увидит: `Вы не внесены в белый список бота.`

Backend делает self-ping по `PUBLIC_BASE_URL` каждые `KEEPALIVE_INTERVAL_SECONDS`, пока процесс уже запущен. Если Render-сервис все равно засыпает, добавьте внешний cron/uptime check каждые 3-5 минут. Самый легкий вариант для поддержания процесса живым: `GET https://fsfwfwbha.onrender.com/api/health`. Вариант, который сразу запускает скан и алерты: `GET https://fsfwfwbha.onrender.com/api/cron/research?secret=<CRON_SECRET>`.

## API

- `GET /api/health` - статус сервиса.
- `GET /api/catalog` - все справочники фильтров.
- `GET /api/results` - лучшие варианты с фильтрами `collectionNames`, `backdropNames`, `modelNames`.
- `POST /api/research/run` - ручной запуск ресерча.
- `GET|POST /api/cron/research?secret=<CRON_SECRET>` - защищенный запуск ресерча и Telegram-алертов для внешнего cron.

## Масштабирование

Новые фильтры добавляются через справочник и `FilterRequest`. Новые источники данных подключаются реализацией сервиса с тем же контрактом, что `MrktClient`. Сравнение цен изолировано в `DealAnalyzer`.
