from google import genai

client = genai.Client(api_key="AIzaSyAxMhNuNY1kXMVi_a3rNZTL5g4ltyXWncY")

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Say hello and confirm you can process Lua code."
)

print(response.text)