import asyncio
import websockets
import json
import logging
import os
from aiohttp import web
import uuid
import aiohttp_cors
from dataclasses import dataclass, asdict
from typing import Dict, Optional
import datetime
import pytz
import requests
from urllib.parse import quote_plus  # For URL encoding


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


class DiagnosticServer:
    def __init__(self):
        self.clients: Dict[str, Client] = {}

    async def register_client(self, websocket: websockets.WebSocketServerProtocol, data: dict):
        """Register a new client"""
        client = Client(
            agreement_id=data['agreement_id'],
            city=data['city'],
            os=data['os'],
            hostname=data['hostname'],
            websocket=websocket,
            last_seen=datetime.datetime.now()
        )
        self.clients[websocket.id] = client
        logging.info(f"New client registered: agr = {client.agreement_id}, city = {client.city}")

    async def unregister_client(self, websocket: websockets.WebSocketServerProtocol):
        """Remove a client when they disconnect"""
        if websocket.id in self.clients:
            client = self.clients.pop(websocket.id)
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
                            message_parts = self.format_ping_message(data, json_result, current_time)
                        elif data['command'] == "tracert":
                            message_parts = self.format_tracert_message(data, json_result, current_time)
                            
                        send_message = "".join(message_parts)

                        # URL encode the message
                        encoded_message = quote_plus(send_message)

                        # Split the message if it's too long
                        max_length = 4000  # Leave some buffer
                        for i in range(0, len(encoded_message), max_length):
                            chunk = encoded_message[i:i + max_length]
                            telegram_url = f'https://api.telegram.org/bot7486482278:AAFrm6uf7lrRcLz03OpFyxO8j6VwO7qjyT8/sendMessage?chat_id=-4624389885&parse_mode=HTML&text={chunk}'
                            response = requests.get(telegram_url)
                            if response.status_code != 200:
                                logging.error(f"Telegram API Error: {response.status_code} - {response.text}")
                                break
                
                except json.JSONDecodeError:
                    logging.error("Received invalid JSON")
        except websockets.ConnectionClosed:
            logging.info("Client connection closed")
        finally:
            await self.unregister_client(websocket)

    def format_ping_message(self, data, json_result, current_time):
        try:
            return [  # Always return a list
                "<b>⚡ Пинг завершен</b>\n\n",
                f"<b>Город:</b> {data['city']}\n",
                f"<b>Договор:</b> {data['agreement']}\n",
                f"<b>Ресурс:</b> {data['target']}\n\n",
                "<b>Результат</b>\n",
                f"Отклик: {json_result['min_rtt']}мс / {json_result['avg_rtt']}мс / {json_result['max_rtt']}мс\n",
                f"Потери: {json_result['packet_loss']}%\n\n",
                f"<i>Время выполнения теста: {current_time}</i>"
            ]
        except Exception as e:  # Handle any exceptions
            logging.error(f"Error formatting ping message: {e}")
            return ["<b>⚡ Error formatting ping results</b>", str(e)] # Return a list with error message

    def format_tracert_message(self, data, json_result, current_time):
        try:
            trace_output = ""
            if isinstance(json_result, list):
                for hop in json_result:
                    rtt = hop.get('rtt')
                    if isinstance(rtt, (int, float)):  # Check if rtt is a number
                        rounded_rtt = round(rtt)  # Round the RTT
                        trace_output += f"{hop.get('hop', '')}. {hop.get('ip', '')} ({rounded_rtt}ms)\n"
                    else:  # Handle cases where rtt is not a number (e.g., "*")
                        trace_output += f"{hop.get('hop', '')}. {hop.get('ip', '')} ({rtt})\n" # Keep original value

            else:
                trace_output = str(json_result)  # Handle if json_result isn't a list

            return [
                "<b>⚡ Трассировка завершена</b>\n\n",
                f"<b>Город:</b> {data['city']}\n",
                f"<b>Договор:</b> {data['agreement']}\n",
                f"<b>Ресурс:</b> {data['target']}\n\n",
                "<b>Результат</b>\n",
                f"<pre>{trace_output}</pre>\n",
                f"<i>Время выполнения теста: {current_time}</i>"
            ]
        except Exception as e:
            logging.error(f"Error formatting tracert message: {e}")
            return ["<b>⚡ Error formatting tracert results</b>", str(e)]
    
    async def send_command(self, client_id: uuid.UUID, command: str, target: str):
        """Send a diagnostic command to a specific client"""
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
                return {"status": "success", "message": f"Command sent to {client.hostname}"}
            except websockets.ConnectionClosed:
                await self.unregister_client(client.websocket)
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
        client_id_str = data.get('client_id') # Get the string representation
        command = data.get('command')
        target = data.get('target')

        if not all([client_id_str, command, target]): # Check for string representation
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
            client_id = uuid.UUID(client_id_str) # Parse back to UUID object
        except ValueError: # Handle invalid UUID strings
            return web.json_response(
                {"status": "error", "message": "Invalid client ID"},
                status=400
            )

        result = await request.app['server'].send_command(client_id, command, target)
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
        '0.0.0.0',
        8765
    )

    # Start HTTP server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 1111)

    # Start both servers
    await site.start()
    logging.info("HTTP server started on http://0.0.0.0:1111")
    logging.info("WebSocket server started on ws://0.0.0.0:8765")

    # Keep the server running
    await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(start_server())