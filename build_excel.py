from dataclasses import dataclass
from typing import Dict, Set, List, Tuple
import pandas as pd
from precalculos import Precalc
from read_instances import ProblemInstance
from collections import defaultdict, Counter
import numpy as np


@dataclass
class export_results:
    # Outputs
    df_assign: pd.DataFrame      # >>> Formato ANCHO (Employee, L, Ma, Mi, J, V, …)
    df_groups: pd.DataFrame
    df_summary: pd.DataFrame 

def count_isolated_employees(df_assign: pd.DataFrame) -> int:  
    """
    Regla:
      1) Si en un (Group, Day) todos están en zonas distintas -> todos son aislados.
      3) Si existe al menos una zona con >1 empleado, los empleados en zonas con
         exactamente 1 persona se cuentan como aislados. Los que están en zonas con
         >=2 personas NO son aislados.
    Notas:
      - Requiere columnas: ['Group','Day','Zone','Employee'].
      - Filas con Zone nula se ignoran para el conteo (no aportan zonas).
    Retorna:
      - Entero con la suma total de aislados.
    """
    needed = {'Group','Day','Zone','Employee'}
    missing = needed - set(df_assign.columns)
    if missing:
        raise ValueError(f"Faltan columnas en df_assign: {missing}")

    # Usamos solo filas con zona conocida
    df = df_assign[df_assign['Zone'].notna()].copy()

    # Conteos por (Group, Day, Zone)
    ctz = (
        df.groupby(['Group','Day','Zone'], dropna=False)
          .size()
          .reset_index(name='n')
    )

    # (Group, Day) candidatos: usan más de 2 zonas
    zones_per_gd = (
        ctz.groupby(['Group','Day'])['Zone']
           .nunique()
           .reset_index(name='num_zones')
    )
    cand = zones_per_gd[zones_per_gd['num_zones'] > 1][['Group','Day']]
    if cand.empty:
        return 0

    ctz = ctz.merge(cand, on=['Group','Day'], how='inner')

    # Aislados por (Group, Day):
    # - si todas las zonas tienen n==1 -> aislados = total del grupo ese día
    # - si hay zonas con n>1 -> aislados = # de personas en zonas con n==1
    def _isolados_grupo_dia(g):
        singles = (g['n'] == 1)
        if singles.all():
            return int(g['n'].sum())
        return int(g.loc[singles, 'n'].sum())

    isolated_total = int(
        ctz.groupby(['Group','Day']).apply(_isolados_grupo_dia, include_groups=False).sum()
        )
    
    return isolated_total   


def build_outputs(inst: ProblemInstance, pre: Precalc, group_meeting_day: Dict[str, str], schedule_by_employee: Dict[str, Set[str]], 
                  assignments: List[Tuple[str, str, str]], df_assign: pd.DataFrame) -> export_results:    
    """
    Construye los tres DataFrames exigidos por la plantilla:
      1) EmployeeAssignment (ANCHO): columnas = ['Employee'] + list(inst.days)
         Valor = desk si (employee, day) asignado; 'None' si no asiste ese día.
      2) Groups Meeting day: (Group, Day)
      3) Summary:
         - Valid assignments = # (e,d) con Desk != 'none' y compatible (desk ∈ desks_by_employee[e])
         - Employee preferences = # (e,d) de Fase 2 donde d ∈ days_by_employee[e]
         - Isolated employees = suma empleado–día donde e es el único de su grupo asistiendo ese día
    """
    # ---- sets para rapidez ----
    days_by_employee = {e: set(v) for e, v in inst.days_by_employee.items()}
    desks_by_employee = {e: set(v) for e, v in inst.desks_by_employee.items()}
    employees_by_group = {g: set(v) for g, v in inst.employees_by_group.items()}

    # ================= EmployeeAssignment (ANCHO) =================
    # Mapa (Employee, Day) -> Desk
    # Si hubiera duplicados (no debería), nos quedamos con el último
    ed_to_desk = {}
    for e, d, desk in assignments:
        ed_to_desk[(e, d)] = str(desk)

    # Construimos filas: una por empleado, columnas por día
    rows = []
    day_list = list(inst.days)  # respeta el orden definido en la instancia
    for e in sorted(inst.employees):
        row = {"Employee": e}
        for d in day_list:
            row[d] = ed_to_desk.get((e, d), "None")
        rows.append(row)

    df_assign_wide = pd.DataFrame(rows, columns=["Employee"] + day_list)

    # ================= Groups Meeting day =================
    df_groups = pd.DataFrame(
        [(g, d) for g, d in group_meeting_day.items()],
        columns=["Group", "Day"]
    ).sort_values("Group")

    # ================= Summary =================
    # -- 1) Valid assignments (desde 'assignments' largos)
    valid_assignments = 0
    for e, d, desk in assignments:
        if desk != "none" and desk in desks_by_employee.get(e, set()):
            valid_assignments += 1

    # -- 2) Employee preferences (desde schedule_by_employee)
    prefs_count = 0
    for e, days in schedule_by_employee.items():
        prefs_count += sum(1 for d in days if d in days_by_employee.get(e, set()))
    employee_preferences = int(prefs_count)

    # -- 3) Isolated employees (suma empleado–día): fuera del núcleo (1–2 zonas sucesivas) del grupo ese día
    isolated_employee_days = count_isolated_employees(df_assign)

    df_summary = pd.DataFrame([{
        "Valid assignments": int(valid_assignments),
        "Employee preferences": int(employee_preferences),
        "Isolated employees": int(isolated_employee_days),
    }])

    return export_results(
        df_assign=df_assign_wide,
        df_groups=df_groups,
        df_summary=df_summary
    )


def export_solution_excel(path, df_assign, df_groups, df_summary):
    """
    Exporta a Excel con las TRES hojas con nombres EXACTOS:
      - 'EmployeeAssignment'  (ANCHO)
      - 'Groups Meeting day'
      - 'Summary'
    """
    # Fallback de motor de Excel
    try:
        writer = pd.ExcelWriter(path, engine="xlsxwriter")
    except ModuleNotFoundError:
        writer = pd.ExcelWriter(path, engine="openpyxl")

    with writer as w:
        df_assign.to_excel(w, index=False, sheet_name="EmployeeAssignment")
        df_groups.to_excel(w, index=False, sheet_name="Groups Meeting day")
        df_summary.to_excel(w, index=False, sheet_name="Summary")

        # Autoajuste simple
        for sheet_name, df in {
            "EmployeeAssignment": df_assign,
            "Groups Meeting day": df_groups,
            "Summary": df_summary
        }.items():
            ws = w.sheets[sheet_name]
            for i, col in enumerate(df.columns):
                width = max(12, min(40, int(df[col].astype(str).map(len).max() if not df.empty else 12) + 2))
                ws.set_column(i, i, width)
