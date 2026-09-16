import gradio as gr
from main import process_agent_logic, get_ai_response
import time


def respond(message, history):
    return get_ai_response(message, history)
demo = gr.ChatInterface(fn=respond).queue()
demo.launch()
# 1 "Agent Brain"
def my_agent_logic(message, history):
    print(f"DEBUG: Received message: {message}")
    time.sleep(1)
    return f"I received your message: {message}. I am now processing your request."
# 2. Setup the interface with .queue()
demo = gr.ChatInterface(
    fn=my_agent_logic,
    title="My AI Agent",
    description="Ask me anything!"
).queue()
# 3. Launch
if __name__ == "__main__":
    demo.launch()