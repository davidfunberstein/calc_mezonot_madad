import streamlit as st
import requests
from datetime import datetime, timedelta
import pandas as pd
import xml.etree.ElementTree as ET


# --- הגדרות ---
DATA_GOV_IL_API_URL = "https://api.cbs.gov.il/index/data/price"
CPI_RESOURCE_ID = "120010"

# --- פונקציות עזר לטיפול בתאריכים ---
def get_date_for_cpi_lookup(year, month):
    return f"{year:04d}{month:02d}"


# --- פונקציה לשליפת מדד המחירים לצרכן מהלמ"ס ---
@st.cache_data(ttl=timedelta(hours=12))
def get_cpi_value_and_base(year, month):
    period_str = get_date_for_cpi_lookup(year, month)
    query_params = {
        "id": CPI_RESOURCE_ID,
        "format": "xml",
        "download": "false",
        "period": period_str,
    }

    try:
        response = requests.get(DATA_GOV_IL_API_URL, params=query_params)
        response.raise_for_status()
        xml_data = response.text

        root = ET.fromstring(xml_data)

        xpath_query = f".//DateMonth[year='{year}'][month='{month}']"
        date_month_element = root.find(xpath_query)

        if date_month_element is not None:
            value_element = date_month_element.find('currBase/value')
            base_desc_element = date_month_element.find('currBase/baseDesc')

            cpi_value = float(value_element.text) if value_element is not None and value_element.text else None
            base_desc = base_desc_element.text if base_desc_element is not None and base_desc_element.text else None

            if cpi_value is not None and base_desc is not None:
                return cpi_value, base_desc
            else:
                return None, None
        else:
            return None, None

    except requests.exceptions.RequestException as e:
        return None, None
    except ET.ParseError as e:
        return None, None
    except (ValueError, KeyError) as e:
        return None, None


# --- פונקציה לחישוב סכום מוצמד ביחס לבסיס קבוע (הצמדה חוזרת לבסיס) ---
def calculate_indexed_amount_from_fixed_base(
    base_amount,
    fixed_base_cpi_value,       # מדד CPI של נקודת הבסיס הקבועה (לדוגמה, מדד מרץ 2024)
    fixed_base_cpi_base_desc,   # תיאור בסיס CPI של נקודת הבסיס הקבועה
    current_period_cpi_value,          # מדד CPI של התקופה הנוכחית (לדוגמה, מדד יוני 2024)
    current_period_cpi_base_desc      # תיאור בסיס CPI של התקופה הנוכחית
):
    """
    מחשבת את הסכום המוצמד מחדש בהתבסס על סכום בסיס קבוע ומדד בסיס קבוע,
    ביחס למדד של התקופה הנוכחית.
    """
    if (
        fixed_base_cpi_value is None
        or current_period_cpi_value is None
        or fixed_base_cpi_value == 0
    ):
        return None

    indexed_amount = base_amount * (current_period_cpi_value / fixed_base_cpi_value)

    return indexed_amount


# --- פונקציה למציאת חודש המדד הרלוונטי לתאריך אפקטיבי/עדכון ---
def get_cpi_month_for_effective_date(effective_date):
    """
    מחזירה את השנה והחודש של המדד הרלוונטי לתאריך העדכון הנתון.
    המדד מתפרסם ב-15 לחודש עבור חודשיים קודם.
    כלומר, עבור תאריך אפקטיבי/עדכון בתחילת חודש X, המדד הרלוונטי הוא של חודש X-2.
    """
    cpi_date = effective_date - pd.DateOffset(months=2)
    
    return cpi_date.year, cpi_date.month


# --- לוגיקה של אפליקציית Streamlit ---
def main():
    st.set_page_config(
        page_title="מחשבון מזונות מוצמד למדד", page_icon="📈", layout="centered"
    )

    st.title("מחשבון מזונות מוצמד למדד המחירים לצרכן")
    st.markdown(
        """
        כלי זה מציג את סכום המזונות המעודכן על בסיס מדד המחירים לצרכן.
        **הבהרה:** אתר זה מציג מידע חישובי בלבד ואינו מהווה ייעוץ משפטי.
        החישוב בפועל של דמי המזונות ותנאי ההצמדה נקבעים על פי פסק דין או הסכם משפטי,
        וייתכנו הבדלים בהתאם לנסיבות הספציפיות. מומלץ להתייעץ עם עורך דין.
        """
    )

    st.header("הגדרות בסיסיות")

    base_mizono_amount = st.number_input(
        "סכום מזונות בסיסי (ש\"ח):",
        min_value=1.0,
        value=4000.0,
        step=100.0,
        format="%.2f",
    )

    col1, col2 = st.columns(2)
    with col1:
        # המשתמש בוחר את חודש תוקף פסק הדין/קביעת הסכום.
        # המדד הרלוונטי שישמש כ"מדד בסיס קבוע" לכל ההצמדות,
        # יחושב כחודשיים לפני תאריך זה.
        base_effective_month_input = st.selectbox(
            "חודש תוקף פסק הדין/ההסכם (תאריך אפקטיבי):",
            options=list(range(1, 13)),
            index=4,  # מאי (חודש 5)
        )
    with col2:
        base_effective_year_input = st.number_input(
            "שנת תוקף פסק הדין/ההסכם:",
            min_value=1990,
            max_value=datetime.now().year,
            value=2024,
            step=1,
        )
    
    update_frequency_months = st.selectbox(
        "תדירות עדכון (חודשים - קובע את נקודות ההצמדה בפועל):", options=[1, 3, 6, 12], index=1
    )

    st.divider()
    st.header("תוצאות החישוב")

    if st.button("חשב סכום מזונות מעודכן"):
        with st.spinner("שולף נתונים ומבצע חישוב..."):
            base_effective_year = int(base_effective_year_input)
            base_effective_month = int(base_effective_month_input)
            # זהו תאריך תוקף פסק הדין/ההסכם (תאריך אפקטיבי)
            base_effective_date_obj = datetime(base_effective_year, base_effective_month, 1)
            
            # המדד שישמש כ"מדד בסיס קבוע" לכל ההצמדות,
            # נגזר מתאריך התוקף של פסק הדין (חודשיים לפניו).
            fixed_base_cpi_year, fixed_base_cpi_month = get_cpi_month_for_effective_date(base_effective_date_obj)
            
            fixed_base_cpi_value, fixed_base_cpi_base_desc = get_cpi_value_and_base(fixed_base_cpi_year, fixed_base_cpi_month)

            if fixed_base_cpi_value is None:
                st.error(
                    f"שגיאה: לא ניתן היה לשלוף את מדד הבסיס הקבוע עבור {fixed_base_cpi_month:02d}/{fixed_base_cpi_year}. אנא וודא את התאריך."
                )
                return

            st.info(f"מדד בסיס קבוע (נגזר מתאריך התוקף {base_effective_month:02d}/{base_effective_year}): **{fixed_base_cpi_value:.2f}** (חודש המדד: {fixed_base_cpi_month:02d}/{fixed_base_cpi_year}, בסיס: {fixed_base_cpi_base_desc})")

            # --- חישוב הסכום העדכני ביותר (התוצאה הסופית) ---
            today = datetime.now()
            
            # המדד שיש להצמיד אליו היום, נגזר מתאריך היום (חודשיים לפניו).
            current_period_cpi_year, current_period_cpi_month = get_cpi_month_for_effective_date(today)
            
            current_period_cpi_value_for_final_calc, current_period_cpi_base_desc_for_final_calc = get_cpi_value_and_base(
                current_period_cpi_year, current_period_cpi_month
            )

            # לוגיקה לטיפול במדד חסר עבור החישוב הסופי
            if current_period_cpi_value_for_final_calc is None:
                st.warning(
                    f"אזהרה: המדד לחודש {current_period_cpi_month:02d}/{current_period_cpi_year} טרם פורסם. "
                    f"סכום המזונות המעודכן יוצג לפי העדכון האחרון ללא שינוי. יש ללחוץ שוב כאשר המדד יפורסם."
                )
                # במקרה של חוסר מדד לחישוב הסופי, נניח שהסכום הוא הסכום הבסיסי (ללא שינוי)
                # ולא ננסה לחשב הצמדה עם מדד קודם
                final_updated_mizono_amount = base_mizono_amount 
                # כדי להציג הודעה למשתמש
                current_period_cpi_value_for_final_calc = "טרם פורסם"
                current_period_cpi_base_desc_for_final_calc = "טרם פורסם"
            else:
                st.info(
                    f"מדד עדכון נוכחי (נגזר מהיום): **{current_period_cpi_value_for_final_calc:.2f}** (חודש המדד: {current_period_cpi_month:02d}/{current_period_cpi_year}, בסיס: {current_period_cpi_base_desc_for_final_calc})"
                )
                # חישוב הסכום הסופי ביחס לבסיס הקבוע
                final_updated_mizono_amount = calculate_indexed_amount_from_fixed_base(
                    base_mizono_amount,
                    fixed_base_cpi_value,
                    fixed_base_cpi_base_desc,
                    current_period_cpi_value_for_final_calc,
                    current_period_cpi_base_desc_for_final_calc
                )
            
            if final_updated_mizono_amount is not None:
                st.success(
                    f"**סכום המזונות המעודכן כיום הוא: {final_updated_mizono_amount:.2f} ש\"ח**"
                )

                # חישוב תאריך העדכון הבא המשוער (בהתאם לתדירות שהוזנה)
                next_update_display_date = base_effective_date_obj
                while next_update_display_date <= today:
                    next_update_display_date += pd.DateOffset(months=update_frequency_months)
                
                st.info(f"**תאריך העדכון הבא המשוער (לפי תדירות שהוזנה):** {next_update_display_date.strftime('%d/%m/%Y')}")


                st.subheader("היסטוריית עדכונים מפורטת (לפי תדירות העדכון)")
                st.markdown(
                    """
                    טבלה זו מציגה את סכום המזונות המוצמד בנקודות העדכון, בהתאם לתדירות העדכון שהוזנה.
                    הסכום נשאר קבוע בין נקודות עדכון, והוא מוצמד תמיד למדד הבסיס הקבוע.
                    """
                )

                # בניית טבלה להצגת היסטוריית עדכונים
                history_data = []
                
                # הסכום המוצג בחודש נתון, שיתעדכן רק בנקודות עדכון רשמיות
                current_displayed_amount_in_history = base_mizono_amount 
                
                # תאריך תחילת הסריקה עבור הטבלה: תאריך תוקף פסק הדין/ההסכם
                current_scan_date = base_effective_date_obj
                
                # תאריך נקודת העדכון הבאה שבה אמור להתבצע חישוב הצמדה
                next_update_calc_date = base_effective_date_obj # מתחיל מנקודת הבסיס

                while current_scan_date <= today:
                    # בדוק אם החודש הנוכחי הוא נקודת עדכון רשמית
                    is_official_update_point = False
                    if (current_scan_date.year == next_update_calc_date.year and 
                        current_scan_date.month == next_update_calc_date.month):
                        is_official_update_point = True
                    
                    if is_official_update_point:
                        # זהו תאריך עדכון. ננסה לחשב את הסכום המוצמד.
                        
                        # המדד שיש להצמיד אליו, נגזר מתאריך העדכון הנוכחי (חודשיים לפניו).
                        cpi_for_update_year, cpi_for_update_month = get_cpi_month_for_effective_date(current_scan_date)
                        cpi_for_update_value, cpi_for_update_base_desc = get_cpi_value_and_base(
                            cpi_for_update_year, cpi_for_update_month
                        )
                        
                        if cpi_for_update_value is None:
                            # אין נתונים עבור חודש זה - נשמור את הסכום הנוכחי ונציין זאת.
                            history_data.append(
                                {
                                    "תאריך עדכון (אפקטיבי)": current_scan_date.strftime("%d/%m/%Y"),
                                    "מדד יחס (חודש/שנה)": f"{cpi_for_update_month:02d}/{cpi_for_update_year} (טרם פורסם)",
                                    "ערך מדד": "טרם פורסם",
                                    "בסיס מדד": "טרם פורסם",
                                    "סכום מעודכן": f"{current_displayed_amount_in_history:.2f} ש\"ח (ללא שינוי מדד)",
                                }
                            )
                            # התקדמות לנקודת העדכון הבאה גם אם המדד חסר
                            next_update_calc_date += pd.DateOffset(months=update_frequency_months)
                            current_scan_date += pd.DateOffset(months=1)
                            continue # המשך ללולאה הבאה (המדד נחשב "לא השתנה")

                        # יש מדד זמין, נבצע חישוב הצמדה
                        calculated_amount = calculate_indexed_amount_from_fixed_base(
                            base_mizono_amount,
                            fixed_base_cpi_value,
                            fixed_base_cpi_base_desc,
                            cpi_for_update_value,
                            cpi_for_update_base_desc
                        )
                        
                        if calculated_amount is not None:
                            current_displayed_amount_in_history = calculated_amount # עדכן את הסכום המוצג
                            history_data.append(
                                {
                                    "תאריך עדכון (אפקטיבי)": current_scan_date.strftime("%d/%m/%Y"),
                                    "מדד יחס (חודש/שנה)": f"{cpi_for_update_month:02d}/{cpi_for_update_year}",
                                    "ערך מדד": f"{cpi_for_update_value:.2f}",
                                    "בסיס מדד": cpi_for_update_base_desc,
                                    "סכום מעודכן": f"{current_displayed_amount_in_history:.2f} ש\"ח",
                                }
                            )
                        else:
                            st.warning(f"לא ניתן לחשב סכום מעודכן עבור תאריך {current_scan_date.strftime('%d/%m/%Y')}.")
                            break # הפסק אם החישוב נכשל
                        
                        # התקדמות לתאריך העדכון הבא
                        next_update_calc_date += pd.DateOffset(months=update_frequency_months)

                    else: # אם זהו לא חודש עדכון רשמי, הסכום נשאר זהה
                        history_data.append(
                            {
                                "תאריך עדכון (אפקטיבי)": current_scan_date.strftime("%d/%m/%Y"),
                                "מדד יחס (חודש/שנה)": "", 
                                "ערך מדד": "", 
                                "בסיס מדד": "", 
                                "סכום מעודכן": f"{current_displayed_amount_in_history:.2f} ש\"ח", # מציג את הסכום האחרון שחושב
                            }
                        )
                    
                    current_scan_date += pd.DateOffset(months=1) # התקדמות לחודש הבא
                
                if history_data:
                    df_history = pd.DataFrame(history_data)
                    # ודא שהעמודה "תאריך עדכון (אפקטיבי)" היא מסוג datetime לצורך מיון נכון
                    df_history['תאריך עדכון (אפקטיבי)'] = pd.to_datetime(df_history['תאריך עדכון (אפקטיבי)'], format="%d/%m/%Y")
                    # מיון בסדר יורד (מהחדש לישן)
                    df_history_sorted = df_history.sort_values(by="תאריך עדכון (אפקטיבי)", ascending=False)
                    # החזר תאריך לפורמט המקורי לצורך תצוגה
                    df_history_sorted['תאריך עדכון (אפקטיבי)'] = df_history_sorted['תאריך עדכון (אפקטיבי)'].dt.strftime("%d/%m/%Y")
                    
                    st.dataframe(df_history_sorted, hide_index=True)
                else:
                    st.write("אין היסטוריית עדכונים להצגה כרגע בטווח המבוקש.")


# הרצת האפליקציה
if __name__ == "__main__":
    main()
