name = input("Как тебя зовут? ")
income = float(input("Сколько у тебя доходов? "))
expenses = float(input("Сколько у тебя расходов? "))

balance = income - expenses

print("Привет,", name)
print("Твой остаток:", balance)