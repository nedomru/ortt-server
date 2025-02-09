import asyncio
import websockets
import json
import logging
from aiohttp import web
import uuid
import aiohttp_cors
from dataclasses import dataclass
from typing import Dict
import datetime
import pytz
import os
from dotenv import load_dotenv

from tg_logging import send_log

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


@dataclass
class Client:
    agreement_id: str
    city: str
    os: str
    hostname: str
    websocket: websockets.WebSocketServerProtocol
    last_seen: datetime.datetime


def format_tracert_message(data, json_result, current_time):
    try:
        trace_output = ""
        if isinstance(json_result, list):
            for hop in json_result:
                min_rtt = hop.get('min_rtt')
                avg_rtt = hop.get('avg_rtt')
                max_rtt = hop.get('max_rtt')
                ip = hop.get('ip', '')

                # Format RTTs, handling non-numeric values like "*"
                rtt_str = ""
                if all(isinstance(r, (int, float)) for r in [min_rtt, avg_rtt, max_rtt]):  # All are numbers
                    rtt_str = f"{round(min_rtt)}мс / {round(avg_rtt)}мс / {round(max_rtt)}мс"
                elif any(isinstance(r, (int, float)) for r in [min_rtt, avg_rtt, max_rtt]):  # Some are numbers
                    rtt_str = f"{min_rtt}мс / {avg_rtt}мс / {max_rtt}мс"
                else:  # All are not numbers
                    rtt_str = f"{min_rtt} / {avg_rtt} / {max_rtt}"  # Keep original value

                trace_output += f"{hop.get('hop', '')}. {ip} ({rtt_str})\n"

        else:
            trace_output = str(json_result)  # Handle if json_result isn't a list

        return [
            "<b>⚡ Трассировка завершена</b>\n\n",
            f"<b>Город:</b> {data['city']}\n",
            f"<b>Договор:</b> <code>{data['agreement']}</code>\n",
            f"<b>Ресурс:</b> {data['target']}\n\n",
            "<b>Результат</b>\n",
            f"<pre>Трассировка до ресурса {data['target']}\n"
            f"{trace_output}</pre>\n",
            f"<i>Время выполнения теста: {current_time}</i>"
        ]
    except Exception as e:
        logging.error(f"Error formatting tracert message: {e}")
        return ["<b>⚡ Error formatting tracert results</b>", str(e)]


def format_ping_message(data, json_result, current_time):
    try:
        return [  # Always return a list
            "<b>⚡ Пинг завершен</b>\n\n",
            f"<b>Город:</b> {data['city']}\n",
            f"<b>Договор:</b> <code>{data['agreement']}</code>\n",
            f"<b>Ресурс:</b> {data['target']}\n\n",
            "<b>Результат</b>\n",
            f"Отклик: {json_result['min_rtt']}мс / {json_result['avg_rtt']}мс / {json_result['max_rtt']}мс\n",
            f"Потери: {json_result['packet_loss']}%\n\n",
            f"<i>Время выполнения теста: {current_time}</i>"
        ]
    except Exception as e:  # Handle any exceptions
        logging.error(f"Error formatting ping message: {e}")
        return ["<b>⚡ Error formatting ping results</b>", str(e)]  # Return a list with error message


class DiagnosticServer:
    def __init__(self):
        self.clients: Dict[str, Client] = {}

    async def register_client(self, websocket: websockets.WebSocketServerProtocol, data: dict):
        """Register a new client"""
        ekb_tz = pytz.timezone('Asia/Yekaterinburg')
        current_time = datetime.datetime.now(ekb_tz).strftime('%H:%M:%S')

        client = Client(
            agreement_id=data['agreement_id'],
            city=data['city'],
            os=data['os'],
            hostname=data['hostname'],
            websocket=websocket,
            last_seen=datetime.datetime.now()
        )
        self.clients[websocket.id] = client

        ready_message = f"""<b>🟢 Новый клиент</b>

<b>Договор:</b> {data['agreement_id']}
<b>Город:</b> {data['city']}
<b>ОС:</b> {data['os']}

<i>Время: {current_time}</i>"""
        await send_log(category="connect", message=ready_message)
        logging.info(f"New client registered: agr = {client.agreement_id}, city = {client.city}")

    async def unregister_client(self, websocket: websockets.WebSocketServerProtocol):
        """Remove a client when they disconnect"""
        ekb_tz = pytz.timezone('Asia/Yekaterinburg')
        current_time = datetime.datetime.now(ekb_tz).strftime('%H:%M:%S')

        if websocket.id in self.clients:
            client = self.clients.pop(websocket.id)

            ready_message = f"""<b>❌ Клиент отключился</b>

ID: <code>{websocket.id}</code>

<i>Время: {current_time}</i>"""
            await send_log(category="connect", message=ready_message)

            logging.info(f"Client disconnected: agr = {client.agreement_id}, city = {client.city}")

    async def handle_websocket(self, websocket: websockets.WebSocketServerProtocol):
        """Handle WebSocket connections"""
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data['type'] == 'registration':
                        await self.register_client(websocket, data['data'])
                    elif data['type'] == 'result':
                        logging.info(f"Received {data['command']} results for {data['target']}")
                        logging.info(data)

                        json_result = json.loads(data['result'])
                        ekb_tz = pytz.timezone('Asia/Yekaterinburg')
                        current_time = datetime.datetime.now(ekb_tz).strftime('%H:%M:%S')

                        message_parts = ["<b>⚡ Неизвестный результат</b>"]

                        if data['command'] == "ping":
                            message_parts = format_ping_message(data, json_result, current_time)
                        elif data['command'] == "tracert":
                            message_parts = format_tracert_message(data, json_result, current_time)

                        ready_message = "".join(message_parts)
                        await send_log(category="ping" if data['command'] == "ping" else "tracert",
                                       message=ready_message)
                except json.JSONDecodeError:
                    logging.error("Received invalid JSON")
        except websockets.ConnectionClosed:
            logging.info("Client connection closed")
        finally:
            await self.unregister_client(websocket)

    async def send_command(self, client_id: uuid.UUID, command: str, target: str):
        """Send a diagnostic command to a specific client"""
        ekb_tz = pytz.timezone('Asia/Yekaterinburg')
        current_time = datetime.datetime.now(ekb_tz).strftime('%H:%M:%S')

        if client_id in self.clients:
            client = self.clients[client_id]
            message = {
                "type": "command",
                "command": command,
                "target": target
            }
            try:
                await client.websocket.send(json.dumps(message))

                logging.info(f"Sent {command} command to {client.hostname}")
                ready_message = f"""<b>🟢 Новый запрос</b>

ID: <code>{client_id}</code>
Команда: {command}
Ресурс: {target}

<i>Время: {current_time}</i>"""
                await send_log(category="request", message=ready_message)
                return {"status": "success", "message": f"Command sent to {client.hostname}"}
            except websockets.ConnectionClosed:
                await self.unregister_client(client.websocket)

                ready_message = f"""<b>❌ Соединение потеряно</b>

ID: {client_id}
Команда: {command}
Ресурс: {target}

<i>Время: {current_time}</i>"""
                await send_log(category="request", message=ready_message)
                return {"status": "error", "message": "Client disconnected"}
        return {"status": "error", "message": "Client not found"}

    def get_clients(self):
        """Get list of connected clients, converting UUIDs to strings"""
        clients_list = []
        for client_id, client in self.clients.items():
            clients_list.append({
                "id": str(client_id),  # Convert UUID to string
                "agreement_id": client.agreement_id,
                "city": client.city,
                "os": client.os,
                "hostname": client.hostname,
                "last_seen": client.last_seen.isoformat()
            })
        return clients_list


async def handle_get_clients(request):
    """HTTP endpoint to get list of clients"""
    clients = request.app['server'].get_clients()
    return web.json_response(clients)


async def handle_send_command(request):
    """HTTP endpoint to send command to client"""
    try:
        data = await request.json()
        client_id_str = data.get('client_id')  # Get the string representation
        command = data.get('command')
        target = data.get('target')

        if not all([client_id_str, command, target]):  # Check for string representation
            return web.json_response(
                {"status": "error", "message": "Missing required parameters"},
                status=400
            )

        if command not in ['tracert', 'ping']:
            return web.json_response(
                {"status": "error", "message": "Invalid command"},
                status=400
            )

        try:
            client_id = uuid.UUID(client_id_str)  # Parse back to UUID object
        except ValueError:  # Handle invalid UUID strings
            return web.json_response(
                {"status": "error", "message": "Invalid client ID"},
                status=400
            )

        send_command = ""
        match command:
            case "ping":
                send_command = "ping -n 30 -l 1200"
            case "tracert": 
                send_command = "tracert /d /4"
        result = await request.app['server'].send_command(client_id, send_command, target)
        return web.json_response(result)
    except Exception as e:
        return web.json_response(
            {"status": "error", "message": str(e)},
            status=500
        )


async def start_server():
    # Create diagnostic server instance
    server = DiagnosticServer()

    # Create web application
    app = web.Application()
    app['server'] = server

    # Setup CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*"
        )
    })

    # Add routes
    app.router.add_get('/api/clients', handle_get_clients)
    app.router.add_post('/api/send-command', handle_send_command)

    async def index_handler(request):
        try:
            # Get the directory where the script is running
            current_directory = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(current_directory, 'index.html')
            return web.FileResponse(file_path)
        except FileNotFoundError:
            return web.Response(status=404, text="File not found")

    app.router.add_get('/', index_handler)  # Serve index.html at the root path
    app.router.add_get('/index.html', index_handler)  # Serve index.html at /index.html too (optional)

    # Apply CORS to routes
    for route in list(app.router.routes()):
        cors.add(route)

    # Create WebSocket server
    ws_server = await websockets.serve(
        lambda websocket: server.handle_websocket(websocket),
        os.getenv('HOST'),
        os.getenv('WEBSOCKET_PORT')
    )

    # Start HTTP server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, os.getenv('HOST'), os.getenv('WEB_PORT'))

    # Start both servers
    await site.start()
    logging.info(f"HTTP server started on {os.getenv('HOST')}:{os.getenv('WEB_PORT')}")
    logging.info(f"WebSocket server started on ws://{os.getenv('HOST')}:{os.getenv('WEBSOCKET_PORT')}")

    # Keep the server running
    await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(start_server())
