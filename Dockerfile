# Dockerfile v1.0
# Используем официальный образ Python 3.11 slim для меньшего размера
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл зависимостей сначала, чтобы использовать кэш Docker
COPY requirements.txt requirements.txt

# Обновляем pip и устанавливаем зависимости Python без кэша
# Добавляем --break-system-packages для совместимости с новыми версиями pip/Debian
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --break-system-packages -r requirements.txt

# Копируем все файлы приложения (app.py, ofc_*.py, mcts_*.py, templates/)
COPY . .

# Указываем Flask, где искать приложение (app.py в корне /app)
ENV FLASK_APP=app.py
# Переменные PORT и WEB_CONCURRENCY будут установлены средой выполнения

# Открываем порт, который будет использовать Gunicorn (из переменной PORT или 10000)
# Это больше для информации, реальное мапирование портов делается при запуске контейнера
EXPOSE ${PORT:-10000}

# Команда по умолчанию для запуска приложения с использованием Gunicorn
# Используем exec для того, чтобы Gunicorn стал PID 1 в контейнере
# Используем переменные окружения PORT и WEB_CONCURRENCY с дефолтными значениями
# Добавляем --timeout для долгих MCTS ходов (120 секунд)
# Используем --log-level info для стандартного уровня логирования
CMD exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --log-level info app:app
