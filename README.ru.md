<p align="center">
  <h1>pyObscuraProto</h1>
  <a href="https://github.com/ObscuraEcosystem/pyObscuraProto/actions"><img src="https://img.shields.io/github/actions/workflow/status/ObscuraEcosystem/pyObscuraProto/autotests.yml?style=for-the-badge&logo=github&label=тесты&color=8A2BE2" alt="Tests"></a>
  <a href="https://github.com/ObscuraEcosystem/pyObscuraProto/stargazers"><img src="https://img.shields.io/github/stars/ObscuraEcosystem/pyObscuraProto?style=for-the-badge&logo=githubsponsors&logoColor=FFFFFF&label=звёзды&color=FFD700" alt="Stars"></a>
  <a href="https://github.com/ObscuraEcosystem/pyObscuraProto/issues"><img src="https://img.shields.io/github/issues/ObscuraEcosystem/pyObscuraProto?style=for-the-badge&logo=openbugbounty&logoColor=FFFFFF&label=issues&color=FF6B6B" alt="Issues"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.13%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/ObscuraEcosystem/pyObscuraProto?style=for-the-badge&logo=libreoffice" alt="License"></a>
</p>

Python-обёртка для C++ библиотеки [ObscuraProto](https://github.com/anomalyco/ObscuraProto) — сквозное шифрование поверх WebSocket.

## Возможности

- **Сквозное шифрование** — протокол Noise (NX pattern) на базе libsodium
- **Аутентификация сервера** — долговременная ключевая пара для подписи, клиент проверяет публичный ключ сервера
- **Автоматическое согласование версий** — клиент и сервер договариваются о версии протокола во время handshake
- **Билдер/ридер бинарных payload'ов** — type-safe fluent API (`PayloadBuilder` / `PayloadReader`)
- **Автоматическая распаковка** — параметры payload'а распаковываются по type hints Python
- **Двунаправленный стриминг** — мультиплексированные потоки данных поверх одного зашифрованного соединения
- **Анонимные и аутентифицированные сессии** — обработка клиентов с/без identity; коллбэки верификации
- **Коллбэки жизненного цикла соединения** — `@server.on_open` / `@server.on_close` для отслеживания подключения и отключения клиентов
- **Система конфигурации** — лимиты скорости, соединений, размера сообщений, таймауты; загрузка из YAML или настройка из Python
- **Полная типизация** — все аннотации типов Python, проверка через pyright
- **Высокая производительность** — C++ ядро через pybind11, GIL освобождается во время I/O
- **Типизированные исключения** — ошибки C++ отображаются в типы Python: `TimeoutError` (встроенный), `LogicError` (`RuntimeError`), `InvalidArgument` (`ValueError`)
- **Таймауты запросов** — таймаут на каждый запрос во всех request API (`sync_request` и async), пробрасывается в C++ ядро; `timeout <= 0` отключает Python-обёртку
- **Вывод ключей из seed** — `Crypto.keypair_from_seed()` (строго 32 байта, иначе `ValueError`) и `Crypto.derive_public_key()`
- **Deadlock-безопасные коллбэки** — блокирующие вызовы (`sync_request`, `stop`, `disconnect`) из коллбэк/IO-потоков поднимают `LogicError` вместо зависания

## Установка

```bash
pip install pyObscuraProto
```

### Сборка из исходников

```bash
git clone --recurse-submodules https://github.com/anomalyco/pyObscuraProto.git
cd pyObscuraProto
python -m venv .venv && source .venv/bin/activate
pip install cmake
pip install -e .
```

Требуется CMake 3.14+ и компилятор C++17.

## Быстрый старт

```python
import asyncio
from ObscuraProto import Server, Client, PayloadBuilder

# Server
async with Server(port=9001) as server:
    @server.on_payload(0x1001)
    def handle(hdl, data: str):
        print(f"Got payload: {data}")
    await asyncio.Future()  # run forever

# Client
async with Client(server.public_key, uri="ws://localhost:9001") as client:
    @client.on_ready
    def ready():
        client.send(PayloadBuilder(0x1001).add_param("Hello").build())
    await asyncio.Future()
```

Больше примеров в [examples/](examples/).

## Streaming API

Двунаправленные мультиплексированные потоки поверх одного зашифрованного соединения.

```python
import asyncio
from ObscuraProto import Server, Client

# --- Server ---
async with Server(port=9006) as server:
    @server.on_incoming_stream
    def handle_stream(stream):
        @stream.on_data
        def on_data(data: bytes):
            stream.write(b"echo: " + data)

        @stream.on_end
        def on_end():
            stream.end()

    await asyncio.Future()  # run forever

# --- Client ---
async with Client(server.public_key, uri="ws://localhost:9006") as client:
    @client.on_ready
    def on_ready():
        stream = client.start_stream()

        @stream.on_data
        def on_data(data: bytes):
            print(f"Echo: {data}")

        stream.write(b"hello")
        stream.end()

    await asyncio.Future()  # run forever
```

Полный пример: [examples/streaming_example.py](examples/streaming_example.py)

Начиная с v1.1.1, `write()`, `end()` и `cancel()` — `noexcept`: после закрытия стрима вызовы молча игнорируются и не бросают исключений.

### Свойства Stream

Класс `Stream` предоставляет несколько полезных свойств:

```python
# Get the stream's op code (if set)
op_code = stream.op_code  # Returns int or None
```

### Запуск стримов с произвольным Op Code

`Server.start_stream()` и `Client.start_stream()` принимают опциональный параметр `stream_op_code`:

```python
# Server starts a stream with a specific op code
stream = server.start_stream(hdl, stream_op_code=0x3001)

# Client starts a stream with a specific op code
stream = client.start_stream(stream_op_code=0x3001)
```

### Обработка стримов по Op Code

Используйте декораторы для обработки стримов с определёнными op_code:

```python
# Server handles authenticated streams with specific op codes
@server.on_stream(0x3001)
def handle_stream_3001(stream):
    @stream.on_data
    def on_data(data: bytes):
        print(f"Received on stream 0x3001: {data}")

# Server handles anonymous streams with specific op codes
@server.on_anon_stream(0x4001)
def handle_anon_stream_4001(stream):
    @stream.on_data
    def on_data(data: bytes):
        print(f"Received anonymous on stream 0x4001: {data}")

# Client handles incoming streams from server with specific op codes
@client.on_stream(0x3001)
def handle_incoming_stream_3001(stream):
    @stream.on_data
    def on_data(data: bytes):
        print(f"Received from server on stream 0x3001: {data}")
```

## Асинхронная поддержка

Библиотека предоставляет полную асинхронную поддержку для современных Python-приложений:

- **attach_event_loop()** — привязка коллбэков к event loop asyncio для потокобезопасной диспетчеризации
- **async_request()** — отправка запросов с получением фьючерсов для ответов. C++-сторона сразу возвращает `CppPayloadFuture`; ответ ожидается через `asyncio.Future`, который наполняется через `loop.call_soon_threadsafe` — event loop не поллит в цикле, и **ни один поток thread pool'а не блокируется** в ожидании. Принимает параметр `timeout` (секунды, по умолчанию 30 с) и поднимает `ObscuraProto.TimeoutError`, если удалённая сторона так и не ответила.
- **async_write()**, **async_end()**, **async_cancel()** — асинхронные версии операций stream I/O
- **async_start_stream()** — асинхронная версия `start_stream()`, которая не блокирует event loop
- **async_request_to_identity()** — отправка запросов клиенту, идентифицированному по публичному ключу (async; использует тот же awaitable-мост и параметр `timeout`, что и `async_request()`)
- **Контекстные менеджеры** — `async with Server(port=...)` и `async with Client(pk, uri=...)` для автоматического управления ресурсами

Пример async-настройки сервера:

```python
import asyncio
from ObscuraProto import Server, Client, PayloadBuilder

# Server
async with Server(port=9001) as server:
    @server.on_payload(0x1001)
    async def handle(hdl, data: str):
        result = await process_data(data)
        server.send(hdl, PayloadBuilder(0x1002).add_param(result).build())
    await asyncio.Future()  # run forever

# Client
async with Client(server.public_key, uri="ws://localhost:9001") as client:
    @client.on_ready
    def ready():
        client.send(PayloadBuilder(0x1001).add_param("Hello").build())
    await asyncio.Future()  # run forever
```

## Таймауты запросов

Каждый async request API принимает параметр `timeout` в **секундах** (float, по умолчанию `30.0`). Если удалённая сторона не ответила вовремя, поднимается `ObscuraProto.TimeoutError`:

```python
import logging
from ObscuraProto import Client, TimeoutError

logger = logging.getLogger(__name__)

async def request_with_timeout(client: Client, payload) -> None:
    try:
        response = await client.async_request(payload, timeout=5.0)
        print(f"Response: {response.op_code:04x}")
    except TimeoutError:
        logger.warning("request timed out, continuing")
```

Request API с поддержкой таймаута:

- `Client.sync_request(payload, timeout_ms=...)`
- `Client.async_request(payload, timeout=30.0)`
- `Server.async_request(hdl, payload, timeout=30.0)`
- `Server.async_request_to_identity(identity_pk, payload, timeout=30.0)`

Каждый Python API пробрасывает таймаут в C++ ядро: `Client.sync_request` принимает `timeout_ms` (миллисекунды, `0` = без ограничений), а `Client.async_request` и `Server.async_request` принимают `timeout` в **секундах** и передают его как `timeout_ms`. Если `timeout <= 0` или `None`, Python-сторонний `asyncio.wait_for` отключается — таймаутом владеет C++ ядро. По истечении поднимается `ObscuraProto.TimeoutError` (подкласс встроенного `TimeoutError`).

Таймаут запроса по умолчанию задаётся через `Config.timeouts.request_ms` (по умолчанию `30000` мс, `0` = без ограничений, загружается из YAML).

## Логирование

Библиотека использует модуль `logging` Python с логгером `ObscuraProto` и NullHandler по умолчанию. Логирование можно настроить по необходимости:

```python
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ObscuraProto")
logger.setLevel(logging.DEBUG)  # Enable debug logging
```

## Обработка ошибок

Ошибки в хендлерах пробрасываются (не проглатываются молча), если не установлен on_error-хендлер. Исключения следует перехватывать на уровне бизнес-логики. Для кастомной обработки ошибок используйте error-хендлеры:

```python
# Server error handler
@server.on_error
def handle_error(error: Exception):
    print(f"Server callback error: {error}")

# Client error handler
@client.on_error
def handle_error(error: Exception):
    print(f"Client callback error: {error}")

# Stream error handler
@stream.on_error
def handle_error(error: Exception):
    print(f"Stream callback error: {error}")
```

**Возвращаемое значение identity-хендлера:** хендлер `@server.on_client_identity` должен возвращать `bool` — `True` для принятия, `False` для отклонения. Возврат `None` приводится к `False` (и для синхронных, и для async-хендлеров).

### Типизированные исключения

Начиная с 1.1.1, исключения C++ отображаются на типизированные исключения Python вместо общего `RuntimeError`:

| Исключение | Базовый класс Python | Возникает когда |
|---|---|---|
| `ObscuraProto.TimeoutError` | встроенный `TimeoutError` | async-запрос не ответил в течение `timeout` |
| `ObscuraProto.LogicError` | `RuntimeError` | блокирующий вызов из коллбэк/IO-потока (защита от deadlock), либо повторное ожидание одноразового `CppPayloadFuture` |
| `ObscuraProto.InvalidArgument` | `ValueError` | невалидные аргументы из C++ (например, неверный размер ключа) |

Обработчики по базовым классам продолжают работать: `except TimeoutError`, `except RuntimeError` и `except ValueError` перехватывают соответствующие исключения ObscuraProto:

```python
from ObscuraProto import Client, PayloadBuilder, TimeoutError, LogicError

async def guarded_request(client: Client):
    try:
        response = await client.async_request(PayloadBuilder(0x1001).build(), timeout=2.0)
        return response
    except TimeoutError:
        print("server did not answer in time")
    except LogicError:
        print("request already consumed or called from a callback thread")
```

## Анонимные и аутентифицированные сессии

Клиенты, подключающиеся **без** identity-ключа, считаются **анонимными** — их сообщения обрабатываются через анонимные хендлеры. Клиенты с подтверждённым Ed25519 identity считаются **аутентифицированными** и используют обычные хендлеры.

### Анонимные хендлеры

```python
@server.on_anon_payload(0x5001)
def handle_anon_register(hdl: op.ConnectionHdl, data: bytes):
    print(f"Anonymous registration: {data}")
    server.send_anonymous(hdl, op.PayloadBuilder(0x5001).add_param("ok").build())

@server.on_anon_request(0x5002)
def handle_anon_auth(hdl: op.ConnectionHdl, token: str) -> op.Payload:
    return op.PayloadBuilder(0x5003).add_param(True).build()

@server.anon_default_payload_handler
def handle_anon_default(hdl: op.ConnectionHdl, payload: op.Payload):
    print(f"Unhandled anonymous opcode: {payload.op_code:04x}")
```

### Аутентификация клиента

```python
# --- Server ---
server = op.Server()

@server.on_client_identity
def check_identity(hdl: op.ConnectionHdl, pk: op.PublicKey) -> bool:
    # Accept only known public keys
    return pk.data == allowed_key.data

# --- Client ---
client = op.Client(server.public_key)
client.connect("ws://localhost:9001")

# Server can now address this client by identity:
server.send_to_identity(client_pk, payload)
identity = server.get_client_identity(hdl)
```

Полный пример: [client_identity_example.cpp](https://github.com/ObscuraEcosystem/ObscuraProto/blob/main/examples/client_identity_example.cpp)

## Модель потоков и защита от deadlock'ов

C++-слой WebSocket вызывает Python-коллбэки на собственных I/O-потоках. Блокирующие вызовы из такого коллбэка привели бы к самоблокировке (deadlock) — поток, который должен обслужить запрос, сам его и делает. Начиная с 1.1.1 биндинги определяют это с помощью thread-local флага коллбэка и поднимают `ObscuraProto.LogicError` вместо зависания:

- `Client.sync_request()` / `Server.sync_request()` / `sync_request_to_identity()` — поднимают `LogicError` при вызове из коллбэк-потока
- `Server.stop()` и `Client.disconnect()` — поднимают `LogicError` при вызове из коллбэк-потока (защита от self-join)

```python
from ObscuraProto import Client, LogicError

def on_ready(client: Client):
    try:
        client.disconnect()
    except LogicError:
        print("disconnect() is not allowed from a callback thread")
```

Вызов блокирующего `sync_request` из **async-хендлера** (поток event loop'а) вызывает предупреждение («sync_request is blocking and must not be called from an async handler»), поскольку это остановило бы event loop. Внутри хендлеров используйте `await async_request()`:

```python
from ObscuraProto import Server, PayloadBuilder

@server.on_payload(0x1001)
async def handle(hdl, data: str):
    # Wrong: sync_request would stall the event loop / deadlock the I/O thread
    # response = server.sync_request(hdl, PayloadBuilder(0x1002).build())
    # Correct:
    response = await server.async_request(hdl, PayloadBuilder(0x1002).build())
```

Операции со стримами и async-фьючерсы запросов выполняются в отдельных thread pool'ах уровня модуля: одно-потоковый экзекьютор стримов сохраняет FIFO-порядок операций `write`/`end`/`cancel`, а отдельный экзекьютор запросов на 4 потока ожидает C++-фьючерсы ответов, не блокируя event loop.

## Конфигурация

ObscuraProto поддерживает гибкую настройку лимитов скорости, соединений, размера сообщений и таймаутов. Создайте объект `Config` и передайте его в `Server` или `Client`:

```python
cfg = op.Config()

# Rate limiting — token bucket per connection
cfg.rate_limit.messages_per_second = 200
cfg.rate_limit.burst_size = 500

# Connection limits — max per IP and total
cfg.connection_limits.max_per_ip = 20
cfg.connection_limits.max_total = 5000

# Message size limits
cfg.message_limits.max_decrypted_payload = 65535

# Timeouts
cfg.timeouts.idle_ms = 600000      # 10 min idle disconnect
cfg.timeouts.handshake_ms = 15000  # 15 sec handshake timeout

# Protocol versions to support (default: [V1_0, V1_1])
cfg.supported_versions = [op.V1_0, op.V1_1]

server = op.Server(config=cfg)
client = op.Client(server.public_key, config=cfg)
```

Или загрузите из YAML-файла (см. [config_example.yml](https://github.com/ObscuraEcosystem/ObscuraProto/blob/main/config_example.yml)):

```python
cfg = op.Config.from_yaml("path/to/config.yml")
```

| Поле конфига | По умолчанию | Описание |
|---|---|---|
| `rate_limit.enabled` | `true` | Включить/отключить все лимиты |
| `rate_limit.messages_per_second` | `100` | Макс. сообщений на соединение в секунду |
| `rate_limit.burst_size` | `200` | Размер burst для token bucket |
| `rate_limit.handshake_attempts_per_minute` | `10` | Макс. попыток handshake с IP в минуту |
| `rate_limit.connections_per_minute` | `30` | Макс. новых соединений с IP в минуту |
| `connection_limits.max_per_ip` | `10` | Макс. одновременных соединений с одного IP |
| `connection_limits.max_total` | `1000` | Макс. всего одновременных соединений |
| `message_limits.max_ws_frame_size` | `1048576` | Макс. размер WebSocket фрейма (байт) |
| `message_limits.max_decrypted_payload` | `65535` | Макс. размер расшифрованного payload (байт) |
| `timeouts.handshake_ms` | `10000` | Таймаут handshake (мс) |
| `timeouts.idle_ms` | `300000` | Таймаут бездействия (мс) |
| `timeouts.check_interval_ms` | `5000` | Интервал проверки таймаутов (мс) |
| `timeouts.request_ms` | `30000` | Таймаут запроса (мс); `0` = без ограничений |
| `supported_versions` | `[0x0101, 0x0100]` | Поддерживаемые версии протокола (V1_1 предпочтительнее) |

## RateLimiter и SecureBuffer

Отдельные низкоуровневые биндинги, добавленные в 1.1.1. Для встроенной обработки соединений используйте настройки `Config.rate_limit` выше; эти классы предназначены для кастомного ограничения скорости и безопасного хранения ключевого материала.

### RateLimiter

Ограничение скорости по схеме token bucket + скользящее окно, строится из `RateLimitConfig`:

```python
from ObscuraProto import RateLimiter, RateLimitConfig

cfg = RateLimitConfig()
cfg.enabled = True
cfg.messages_per_second = 100
cfg.burst_size = 200
rl = RateLimiter(cfg)

conn_id = rl.register_connection("203.0.113.7")   # returns an int connection id
if rl.check_message_rate(conn_id):
    rl.record_message(conn_id)
rl.unregister_connection(conn_id, "203.0.113.7")
```

Методы: `check_connection_rate(ip)`, `record_connection(ip)`, `check_handshake_rate(ip)`, `record_handshake(ip)`, `check_message_rate(conn_id)`, `record_message(conn_id)`, `check_active_connections(ip)`, `register_connection(ip)`, `unregister_connection(conn_id, ip)`, `active_total()`, `cleanup()`.

### SecureBuffer

Память в куче, выделенная через `sodium_malloc` и обнуляемая через `sodium_memzero` при `clear()` и при уничтожении объекта. Python всегда получает только **копии** содержимого и никогда — ссылку на внутреннюю память:

```python
from ObscuraProto import SecureBuffer

buf = SecureBuffer(32)             # zero-initialized allocation
buf.from_bytes(b"secret-key-material")
data = buf.to_bytes()              # copy — internal memory stays opaque
len(buf)                           # 19
buf.clear()                        # wipes memory with sodium_memzero
```

Дополнительные методы: `resize(new_size)`, `size()`, `empty()`; поддерживает `bytes(buf)` и `len(buf)`.

## Справочник API

| Класс / Функция | Описание |
|---|---|
| `Server` | Зашифрованный WebSocket-сервер. Декораторы: `@server.on_payload(op_code)`, `@server.on_request(op_code)`, `@server.on_open`, `@server.on_close`, `@server.on_client_identity`, `@server.on_incoming_stream`, `@server.on_stream(op_code)`, `@server.on_anon_payload(op_code)`, `@server.on_anon_request(op_code)`, `@server.on_anon_stream(op_code)`, `@server.anon_default_payload_handler`, `@server.on_error`. Запросы: `sync_request(hdl, payload)`, `async_request(hdl, payload, timeout=30.0)`, `async_request_to_identity(identity_pk, payload, timeout=30.0)` |
| `Client(server_pk)` | Зашифрованный WebSocket-клиент. Декораторы: `@client.on_ready`, `@client.on_disconnect`, `@client.on_payload(op_code)`, `@client.on_request(op_code)`, `@client.on_incoming_stream`, `@client.on_stream(op_code)`, `@client.on_error`. Запросы: `sync_request(payload, timeout_ms=0)`, `async_request(payload, timeout=30.0)` |
| `Stream` | Двунаправленный поток данных. Декораторы: `@stream.on_data`, `@stream.on_end`, `@stream.on_cancel`, `@stream.on_error`. I/O: `write()`, `end()`, `cancel()`, `async_write()`, `async_end()`, `async_cancel()`. Свойства: `stream_id`, `op_code`. `write()`/`end()`/`cancel()` — `noexcept`: после закрытия молча игнорируются |
| `PayloadBuilder(opcode)` | Сборка бинарных payload'ов. `add_param(str / int / uint / bool / float / bytes)`, `.build()` |
| `PayloadReader(payload)` | Чтение бинарных payload'ов. `read_string()`, `read_int()`, `read_uint()`, `read_bool()`, `read_float()`, `read_bytes()` |
| `Payload` | Сырой payload с полями `.op_code` и `.parameters`. Есть `.serialize()` / `Payload.deserialize()` |
| `uint` | Маркер типа: `def handler(value: uint)` читает параметр как беззнаковое целое |
| `Config` | Конфигурация сервера/клиента. Подструктуры: `rate_limit`, `connection_limits`, `message_limits`, `timeouts`, `opcodes`, `supported_versions`. Методы: `from_yaml(path)`, `with_defaults()` |
| `Crypto` | Статические криптооперации: `init()`, `generate_kx_keypair()`, `generate_sign_keypair()`, `keypair_from_seed(seed)` (строго 32 байта, иначе `ValueError`), `derive_public_key(privkey)`, `sign()`, `verify()`, `encrypt()`, `decrypt()` — `decrypt()` возвращает `DecryptedResult` |
| `DecryptedResult` | Результат `Crypto.decrypt()`: поля `payload` (`Payload`) и `counter` |
| `RateLimiter(config)` | Лимитер скорости token bucket / скользящее окно, строится из `RateLimitConfig`. Методы: `check_connection_rate(ip)`, `record_connection(ip)`, `check_handshake_rate(ip)`, `record_handshake(ip)`, `check_message_rate(conn_id)`, `record_message(conn_id)`, `check_active_connections(ip)`, `register_connection(ip)`, `unregister_connection(conn_id, ip)`, `active_total()`, `cleanup()` |
| `RateLimitConfig` | Конфигурация для `RateLimiter`: `enabled`, `messages_per_second`, `burst_size`, `handshake_attempts_per_minute`, `connections_per_minute`; статический `defaults()` |
| `SecureBuffer` | Безопасная память в куче (sodium): `SecureBuffer(size=0)`, `to_bytes()`, `from_bytes(data)`, `clear()` (`sodium_memzero`), `resize(new_size)`, `size()`, `empty()`; поддерживает `bytes()` и `len()` |
| `TimeoutError` / `LogicError` / `InvalidArgument` | Типизированные исключения: подклассы встроенного `TimeoutError` / `RuntimeError` / `ValueError` соответственно |
| `KeyPair` / `PublicKey` / `PrivateKey` | Типы ключей с полем `.data` |
| `ConnectionHdl` | Непрозрачный идентификатор соединения для адресации конкретных клиентов |
| `V1_0`, `V1_1` | Константы версий протокола |
| `SUPPORTED_VERSIONS` | Константа с версиями протокола по умолчанию |

## Примеры

| Пример | Описание |
|---|---|
| [python_websocket_example.py](examples/python_websocket_example.py) | Минимальный send/response с авто-распаковкой |
| [request_response_example/](examples/request_response_example/) | Паттерн запрос-ответ (async сервер + клиент) |
| [streaming_example.py](examples/streaming_example.py) | Двунаправленный стриминг echo |
| [stream_opcode_example.py](examples/stream_opcode_example.py) | Стриминг с op_code — два логических канала |
| [client_identity_example.cpp](https://github.com/ObscuraEcosystem/ObscuraProto/blob/main/examples/client_identity_example.cpp) | Анонимная регистрация + аутентифицированная сессия (C++) |

## Разработка

```bash
source .venv/bin/activate
pip install -e .
pre-commit install
```

- **Ruff** — линтинг и форматирование
- **Pyright** — проверка типов
- **pytest** — тестирование (`python -m pytest tests/`)
- **Pre-commit** — автоматические проверки перед каждым коммитом

### CI — `audit_p0`

Набор сценариев `audit_p0` (`tests/audit_p0/run_audit.py`) запускается в основном CI-workflow (`autotests.yml`) как job `audit` — ubuntu-latest, параллельно с `build-and-test`, `timeout-minutes: 25`. Каждый сценарий запускается в отдельном процессе с внешним таймаутом и классифицируется:

| Статус | Значение |
|---|---|
| `PASS` | сценарий завершился штатно и совпал с ожидаемым результатом |
| `FAIL` | сценарий завершился, но наблюдаемый результат разошёлся с ожиданиями |
| `HANG` | сценарий не завершился до внешнего таймаута — dump потоков faulthandler определяется по маркеру `"(most recent call first)"` (CPython 3.13+) |
| `UNKNOWN` | нет пригодного кода завершения — всегда считается красным |

Наблюдаемый статус каждого сценария зафиксирован в таблице `EXPECTED` внутри `run_audit.py` (базлайн v1.1.1, 14 записей). Раннер завершается с кодом `1` при любом расхождении (включая любой `UNKNOWN`) и с `0` при полном совпадении — это и отображает job `audit` в CI. Все 14 сценариев теперь осмысленно ассертят поведение, и `EXPECTED` зелёный: X1–X3 PASS, X4-P1..P6 PASS, X4-P7 HANG (самодекларированный — клиентский таймаут 8 с), X5-S1..S3 PASS, X6 PASS. X6 ассертит пять таймаут-семантик (дистанции ±1 с, тип `ObscuraProto.TimeoutError`); X4-P6 ассертит `LogicError` "Session not ready". Известные GAP: X5-S2 редизайнен — GC при **активном колбэке** не тестируется (сервер не диспатчит хендлеры под `asyncio.run`), вместо этого сценарий проверяет GC при in-flight запросе; X6 — единственный тайминговый сценарий (риск ~5% на нагруженном CI в точке X6-1b). Запуск аудита локально и обновление `EXPECTED` описаны в [docs/development_guide.md](docs/development_guide.md).

Полные правила в [CONTRIBUTING.md](CONTRIBUTING.md).

## Известные проблемы

### C++ Timing Window — гонка в `Server.stop()`

Вызов `Server.stop()` до полной инициализации accept-цикла может зависнуть навсегда (гонка ~20 мс между запуском потока и сигналом остановки).

**Смягчение:** Подержите сервер живым короткое время после завершения handshake перед выходом из контекстного менеджера, либо добавьте небольшую задержку перед остановкой:

```python
async with Server(port=9001) as server:
    # ... setup handlers ...
    await asyncio.sleep(0.1)  # allow accept loop to initialize
    # ... run server ...
# context manager exit calls stop() safely
```

### Flaky Integration Test — `test_full_cycle_v1_1`

Тест `tests/integration/test_full_cycle.py::test_full_cycle_v1_1` чувствителен к таймингу и может периодически падать при высокой нагрузке. При запуске в изоляции проходит стабильно.

```bash
# Run in isolation to verify
python -m pytest tests/integration/test_full_cycle.py::test_full_cycle_v1_1 -v
```

## Лицензия

MIT © 2025 Kretov Artem. Подробнее в [LICENSE](LICENSE).
