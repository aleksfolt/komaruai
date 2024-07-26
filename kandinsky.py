import json
import time
import requests
import base64
from PIL import Image
from io import BytesIO


class Text2ImageAPI:
    def __init__(self, url, api_key, secret_key):
        self.URL = url
        self.AUTH_HEADERS = {
            'X-Key': f'Key {api_key}',
            'X-Secret': f'Secret {secret_key}',
        }

    def get_model(self):
        response = requests.get(self.URL + 'key/api/v1/models', headers=self.AUTH_HEADERS)
        data = response.json()
        return data[0]['id']

    def generate(self, prompt, model, images=1, width=1024, height=1024):
        params = {
            "type": "GENERATE",
            "numImages": images,
            "width": width,
            "height": height,
            "generateParams": {
                "query": prompt
            }
        }
        data = {
            'model_id': (None, model),
            'params': (None, json.dumps(params), 'application/json')
        }
        response = requests.post(self.URL + 'key/api/v1/text2image/run', headers=self.AUTH_HEADERS, files=data)
        data = response.json()
        return data['uuid']

    def check_generation(self, request_id, attempts=10, delay=10):
        image_paths = []
        while attempts > 0:
            response = requests.get(self.URL + 'key/api/v1/text2image/status/' + request_id, headers=self.AUTH_HEADERS)
            data = response.json()
            if data['status'] == 'DONE':
                images = data['images']
                for i, base64_image in enumerate(images):
                    filename = f"image_{request_id}_{i}.jpg"
                    self.save_image(base64_image, filename)
                    image_paths.append(filename)
                return image_paths
            attempts -= 1
            time.sleep(delay)
        return image_paths

    def save_image(self, base64_string, filename):
        image_data = base64.b64decode(base64_string)
        with open(filename, 'wb') as file:
            file.write(image_data)
        return filename
