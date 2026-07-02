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

Проект готов к деплою через Blueprint `render.yaml`.

1. Подключите GitHub репозиторий к Render.
2. Создайте Blueprint из `render.yaml`.
3. В Render Dashboard заполните secret env vars: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`.
4. После первого деплоя укажите домен frontend в `CORS_ORIGINS`, если Render выдаст другое имя сервиса.

## API

- `GET /api/health` - статус сервиса.
- `GET /api/catalog` - все справочники фильтров.
- `GET /api/results` - лучшие варианты с фильтрами `collectionNames`, `backdropNames`, `modelNames`.
- `POST /api/research/run` - ручной запуск ресерча.

## Масштабирование

Новые фильтры добавляются через справочник и `FilterRequest`. Новые источники данных подключаются реализацией сервиса с тем же контрактом, что `MrktClient`. Сравнение цен изолировано в `DealAnalyzer`.
