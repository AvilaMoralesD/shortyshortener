import base64
import json
import random
import string
from urllib.parse import urlparse
import boto3
import botocore

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('url-shortener')

def generate_short_id(length=6):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def save_url_with_retry(table, original_url, max_retries=10):
    for _ in range(max_retries):
        new_id = generate_short_id()
        try:
            table.put_item(
                Item={'id': new_id, 'original_url': original_url},
                ConditionExpression='attribute_not_exists(id)'
            )
            return new_id
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                continue  # Colisión detectada, genera otro ID
            raise e
    raise Exception("No se pudo generar un ID único tras varios intentos")

def lambda_handler(event, context):
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
        "Access-Control-Allow-Headers": "Content-Type"
    }

    # Manejo de preflight CORS
    http_method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method', '')
    if http_method == 'OPTIONS':
        return {"statusCode": 200, "headers": headers, "body": json.dumps({"message": "OK"})}

    path_params = event.get('pathParameters') or {}
    short_id = path_params.get('id')

    # CASO 1: GET /{id} -> Redirección HTTP 301
    if http_method == 'GET' and short_id:
        response = table.get_item(Key={'id': short_id})
        item = response.get('Item')
        
        if not item:
            return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "URL no encontrada"})}
        
        return {
            "statusCode": 301,
            "headers": {
                "Location": item['original_url'],
                "Access-Control-Allow-Origin": "*"
            },
            "body": ""
        }

    # CASO 2: POST /links -> Crear URL acortada
    if http_method == 'POST':
        body_raw = event.get('body', '{}')
        if event.get('isBase64Encoded', False):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
            
        data = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
        original_url = str(data.get('url', '')).strip()

        # =========================================================================
        # VALIDACIONES DE SEGURIDAD (Anti-inyección, XSS, CRLF y bucles)
        # =========================================================================
        # 1. Comprobar que no venga vacía ni exceda una longitud máxima
        if not original_url or len(original_url) > 2048:
            return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "URL no válida o demasiado larga"})}

        # 2. Prevenir inyección de cabeceras HTTP (CRLF injection)
        if any(c in original_url for c in ('\r', '\n', '\0', '\t')):
            return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Caracteres de control no permitidos"})}

        # 3. Bloquear esquemas maliciosos (javascript:, data:, file:)
        parsed = urlparse(original_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Solo se permiten enlaces válidos con http:// o https://"})}

        # 4. Prevenir auto-acortado y bucles infinitos
        if parsed.netloc.lower() == "shortyshortener.me":
            return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "No se pueden acortar enlaces de este mismo dominio"})}
        # =========================================================================

        try:
            new_id = save_url_with_retry(table, original_url)
        except Exception as err:
            return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": str(err)})}

        return {
            "statusCode": 201,
            "headers": headers,
            "body": json.dumps({
                "id": new_id,
                "original_url": original_url,
                "short_url": f"https://shortyshortener.me/{new_id}"
            })
        }

    return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Petición no válida"})}