import os
import speech_recognition as sr
import pyttsx3
import pythoncom
from dotenv import load_dotenv
from groq import Groq


def process_agent_logic(user_input):
    response = "The AI processed: "+ user_input
    return response

current_system_instruction = "You are a helpful, professional assistant."


def get_ai_response(user_input, history):
    global current_system_instruction

    trigger_phrases = ["act like a", "talk like a", "be a", "persona:"]
    user_lower = user_input.lower()

    is_command = any(phrase in user_lower for phrase in trigger_phrases)

    if is_command:
        for phrase in trigger_phrases:
            if phrase in user_lower:
                # Get everything after the trigger phrase
                new_persona = user_input.split(phrase, 1)[1].strip()

                # Update the universal instruction
                current_system_instruction = f"You are {new_persona}. Respond in the style, tone, and character of this persona for all future messages until I tell you otherwise."

                return f"Understood. I am now acting as: {new_persona}."

    # 3. RESET COMMAND
    if "reset" in user_lower or "be normal" in user_lower:
        current_system_instruction = "You are a helpful, professional assistant."
        return "Mode reset. I am back to being your assistant."

    # 4. CHAT WITH THE DYNAMIC INSTRUCTION
    messages = [{"role": "system", "content": current_system_instruction}]

    # Add history
    for pair in history:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            messages.append({"role": "user", "content": str(pair[0])})
            messages.append({"role": "assistant", "content": str(pair[1])})

    messages.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages
    )

    return response.choices[0].message.content
# 1. Setup
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

engine = pyttsx3.init()


def speak(text):
    global engine
    print(f"Agent: {text}")
    pythoncom.CoInitialize()
    engine.say(text)
    engine.runAndWait()
    pythoncom.CoInitialize()


def get_voice_input():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)
        try:
            return recognizer.recognize_google(audio)
        except:
            return None


# Initial instructions
history = [{"role": "system", "content": "You are a helpful assistant."}]

print("Hi! I am Venu AI. How may I help you? (type 'quit' to exit).")

# 2. The Chat Loop
if __name__ == '__main__':
    while True:
        # Ask user for input method
        choice = input("\nChoose input method (text/voice): ").lower().strip()

        if choice == "quit":
            print("Bye bye!")
            break

        user_input = ""
        is_voice = (choice == "voice")

        if choice == "text":
            user_input = input("You: ")
        elif is_voice:
            user_input = get_voice_input()
            if user_input:
                print(f"You (voice): {user_input}")
            else:
                print("Sorry, I couldn't understand that.")
                continue
        else:
            print("Please type 'text' or 'voice'.")
            continue

        # Process message
        history.append({"role": "user", "content": user_input})

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=history
        )
        answer = response.choices[0].message.content

        # Output
        if is_voice:
            speak(answer)
        else:
            print(f"Agent: {answer}")

        history.append({"role": "assistant", "content": answer})