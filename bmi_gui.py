import tkinter as tk
from tkinter import messagebox

def calculate_bmi():
    try:
        weight = float(weight_entry.get())
        height = float(height_entry.get())
        if weight <= 0 or height <= 0:
            messagebox.showerror("Error", "Values must be greater than zero")
            return
        bmi = weight / (height ** 2)
        if bmi < 18.5:
            category = "Underweight"
        elif bmi < 25:
            category = "Normal weight"
        elif bmi < 30:
            category = "Overweight"
        else:
            category = "Obese"
        result_label.config(text=f"BMI: {bmi:.2f}\nCategory: {category}")
    except ValueError:
        messagebox.showerror("Error", "Please enter valid numbers")

# GUI window
root = tk.Tk()
root.title("BMI Calculator")

# Labels
tk.Label(root, text="Weight (kg):").grid(row=0, column=0, padx=10, pady=5)
tk.Label(root, text="Height (m):").grid(row=1, column=0, padx=10, pady=5)

# Entry fields
weight_entry = tk.Entry(root)
weight_entry.grid(row=0, column=1, padx=10, pady=5)
height_entry = tk.Entry(root)
height_entry.grid(row=1, column=1, padx=10, pady=5)

# Button
tk.Button(root, text="Calculate BMI", command=calculate_bmi).grid(row=2, column=0, columnspan=2, pady=10)

# Result label
result_label = tk.Label(root, text="")
result_label.grid(row=3, column=0, columnspan=2, pady=10)

# Run the GUI
root.mainloop()
