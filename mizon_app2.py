import streamlit as st
import requests
from datetime import datetime, timedelta
import pandas as pd
import xml.etree.ElementTree as ET


# --- הגדרות ---
# כתובת ה-API של הלמ"ס (מדד המחירים לצרכן - כללי)
DATA_GOV_IL_API_URL = "https://api.cbs.gov.il/index/data/price"
# מזהה המשאב (Resource ID) של מדד המחירים לצרכן - קוד 120010
CPI_RESOURCE_ID = "120010"  # קוד מדד המחירים לצרכן לפי ה-API של הלמ"ס

# --- פונקציות עזר לטיפול בתאריכים ---
def get_date_for_cpi_lookup(year, month):
    """
    מחזירה את התאריך בפורמט 'YYYYMM' עבור חיפוש המדד ב-API של הלמ"ס.
    """
    return f"{year:04d}{month:02d}"


# --- פונקציה לשליפת מדד המחירים לצרכן מהלמ"ס ---
@st.cache_data(ttl=timedelta(hours=12))
def get_cpi_value_and_base(year, month):
    """
    שולף את מדד המחירים לצרכן (value) ואת תיאור הבסיס (baseDesc)
    עבור חודש ושנה ספציפיים מה-API של הלמ"ס.
    """
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
                st.warning(f"אזהרה: לא נמצא ערך מדד או תיאור בסיס עבור תקופה {period_str} (שנה: {year}, חודש: {month}).")
                return None, None
        else:
            st.warning(f"אזהרה: לא נמצא אלמנט DateMonth עבור תקופה {period_str} (שנה: {year}, חודש: {month}).")
            return None, None

    except requests.exceptions.RequestException as e:
        st.error(f"שגיאה בגישה ל-API של הלמ\"ס (לשליפת מדד ובסיס): {e}")
        return None, None
    except ET.ParseError as e:
        st.error(f"שגיאה בניתוח נתוני ה-XML: {e}")
        st.code(xml_data, language="xml")
        return None, None
    except (ValueError, KeyError) as e:
        st.error(f"שגיאה בעיבוד נתוני ה-API: {e}")
        return None, None


# --- פונקציה לחישוב סכום המזונות המעודכן עם מקדם קשר ---
def calculate_updated_mizono_with_link_factor(base_amount, base_cpi_value, current_cpi_value, link_factor=1.0):
    """
    מחשבת את סכום המזונות המעודכן, כולל מקדם קשר.
    """
    if (
        base_cpi_value is None
        or current_cpi_value is None
        or base_cpi_value == 0
    ):
        return None

    # בהתבסס על נוסחת הדוגמה שלך: סכום חדש = סכום מקורי * ((מדד יעד * מקדם קשר) / מדד בסיס)
    # ההנחה היא שמקדם הקשר משפיע על מדד היעד כדי להביא אותו לאותו "קנה מידה" של מדד הבסיס
    # או שמדד הבסיס צריך להיות מותאם
    # אם המדד של 2024-3 (106) הוא בבסיס ישן, והמדד של 2025-3 (102) הוא בבסיס חדש,
    # ומקדם הקשר (1.074) מחבר בין הבסיס הישן לחדש, אז:
    # סכום חדש = סכום מקורי * (מדד יעד / (מדד בסיס / מקדם קשר))
    # = סכום מקורי * (מדד יעד * מקדם קשר / מדד בסיס)  <-- זו הנוסחה שעובדת עם המספרים שנתת
    
    updated_amount = base_amount * (current_cpi_value * link_factor / base_cpi_value)
    return updated_amount


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

    # קלטים מהמשתמש
    base_mizono_amount = st.number_input(
        "סכום מזונות בסיסי (ש\"ח):",
        min_value=1.0,
        value=4000.0,
        step=100.0,
        format="%.2f",
    )

    # בחירת חודש מדד הבסיס
    col1, col2 = st.columns(2)
    with col1:
        base_month_input = st.selectbox(
            "חודש מדד בסיס (לפי פסק הדין):",
            options=list(range(1, 13)),
            index=4,  # מאי (חודש 5), אינדקס 4
        )
    with col2:
        base_year_input = st.number_input(
            "שנת מדד בסיס (לפי פסק הדין):",
            min_value=1990,
            max_value=datetime.now().year,
            value=2024,
            step=1,
        )
    
    st.subheader("מקדם קשר (אם רלוונטי)")
    st.markdown(
        """
        יש להזין מקדם קשר אם סדרת המדדים השתנתה בין תאריך הבסיס לתאריך העדכון
        והמחשבון הרשמי של הלמ"ס משתמש במקדם זה (לדוגמה: 1.074 למעבר בסיס).
        אם אינך בטוח, השאר 1.0.
        """
    )
    link_factor_input = st.number_input(
        "מקדם קשר:",
        min_value=0.5,
        value=1.074, # ערך ברירת מחדל לפי הדוגמה שסיפקת
        max_value=2.0,
        step=0.001,
        format="%.3f",
    )


    update_frequency_months = st.selectbox(
        "תדירות עדכון (חודשים):", options=[1, 3, 6, 12], index=1  # ברירת מחדל: 3 חודשים
    )

    st.divider()
    st.header("תוצאות החישוב")

    if st.button("חשב סכום מזונות מעודכן"):
        with st.spinner("שולף נתונים ומבצע חישוב..."):
            base_year = int(base_year_input)
            base_month = int(base_month_input)

            # שליפת מדד הבסיס ותיאור הבסיס
            base_cpi_value, base_cpi_base_desc = get_cpi_value_and_base(base_year, base_month)

            if base_cpi_value is None:
                st.error(
                    "שגיאה: לא ניתן היה לשלוף את מדד הבסיס. אנא וודא את התאריך."
                )
                return

            st.info(f"מדד בסיס ({base_month:02d}/{base_year}, בסיס: {base_cpi_base_desc}): **{base_cpi_value:.2f}**")

            # מציאת חודש המדד העדכני ביותר שפורסם
            today = datetime.now()

            # המדד לחודש קודם מתפרסם ב-15 לחודש הנוכחי.
            # אם אנחנו לפני ה-15, המדד האחרון שפורסם הוא עבור חודשיים אחורה.
            # אחרת, עבור חודש אחורה.
            current_cpi_lookup_month = today.month
            current_cpi_lookup_year = today.year

            if today.day < 15:  # המדד לחודש הקודם עדיין לא פורסם
                current_cpi_lookup_month -= 2
            else:  # המדד לחודש הקודם כבר פורסם
                current_cpi_lookup_month -= 1

            # טיפול במעבר שנים
            if current_cpi_lookup_month <= 0:
                current_cpi_lookup_month += 12
                current_cpi_lookup_year -= 1
            
            # וודא שהמדד המבוקש לא עתידי
            if (current_cpi_lookup_year > datetime.now().year) or \
               (current_cpi_lookup_year == datetime.now().year and current_cpi_lookup_month > datetime.now().month):
               current_cpi_lookup_year = datetime.now().year
               current_cpi_lookup_month = datetime.now().month
               # אם היום לפני ה-15 בחודש, המדד האחרון שפורסם הוא חודשיים אחורה
               if datetime.now().day < 15:
                   current_cpi_lookup_month -= 2
                   if current_cpi_lookup_month <= 0:
                       current_cpi_lookup_month += 12
                       current_cpi_lookup_year -= 1
               else: # אם היום אחרי ה-15 בחודש, המדד האחרון שפורסם הוא חודש אחורה
                   current_cpi_lookup_month -= 1
                   if current_cpi_lookup_month <= 0:
                       current_cpi_lookup_month += 12
                       current_cpi_lookup_year -= 1


            st.write(
                f"**מדד נוכחי לחישוב:** מנסה לשלוף מדד עבור {current_cpi_lookup_month:02d}/{current_cpi_lookup_year}..."
            )
            current_cpi_value, current_cpi_base_desc = get_cpi_value_and_base(
                current_cpi_lookup_year, current_cpi_lookup_month
            )

            if current_cpi_value is None:
                st.warning(
                    "אזהרה: לא ניתן היה לשלוף את המדד העדכני ביותר. ייתכן שהוא טרם פורסם או שיש שגיאה ב-API."
                )
                st.error("לא ניתן לבצע חישוב ללא מדד עדכני זמין.")
                return

            st.info(
                f"מדד עדכני ({current_cpi_lookup_month:02d}/{current_cpi_lookup_year}, בסיס: {current_cpi_base_desc}): **{current_cpi_value:.2f}**"
            )

            # חישוב הסכום המעודכן עם מקדם הקשר
            updated_mizono_amount = calculate_updated_mizono_with_link_factor(
                base_mizono_amount, base_cpi_value, current_cpi_value, link_factor_input
            )

            if updated_mizono_amount is not None:
                st.success(
                    f"**סכום המזונות המעודכן הוא: {updated_mizono_amount:.2f} ש\"ח**"
                )

                # חישוב תאריך העדכון הבא המשוער (בהתאם לתדירות)
                # נתחיל מתאריך מדד הבסיס ונלך קדימה בקפיצות של update_frequency_months
                next_update_date = datetime(base_year, base_month, 1)

                # קירוב לתאריך שבו יחול העדכון הבא
                # בפועל, צריך לוודא מול פסק הדין מתי הוא "נקודת העדכון"
                while next_update_date <= today:
                    # הוספת מספר חודשים, וטיפול במעבר שנה
                    next_month = next_update_date.month + update_frequency_months
                    next_year = next_update_date.year
                    while next_month > 12:  # לטפל ביותר משנה
                        next_month -= 12
                        next_year += 1

                    # Streamlit עשוי לרוץ מחדש, לכן נשמור את הערך
                    st.session_state.next_update_date = datetime(
                        next_year, next_month, 1
                    )
                    next_update_date = st.session_state.next_update_date

                st.info(f"**תאריך העדכון הבא המשוער:** {next_update_date.strftime('%d/%m/%Y')}")

                st.subheader("היסטוריית עדכונים (הדמיה)")
                st.write("זוהי הדמיה של עדכונים היסטוריים על בסיס נתוני הלמ\"ס והמקדם שהוזן. ייתכנו אי-דיוקים אם מקדמי הקשר משתנים לאורך ציר הזמן.")

                # בניית טבלה להצגת היסטוריית עדכונים
                history_data = []
                current_calc_date = datetime(base_year, base_month, 1)
                
                # לצורך הדמיה, נציג עדכונים קדימה עד לתאריך הנוכחי
                while current_calc_date <= today:
                    # חודש המדד ששימש לחישוב בפועל (חודש לפני כניסת העדכון לתוקף)
                    cpi_lookup_month_for_history = current_calc_date.month
                    cpi_lookup_year_for_history = current_calc_date.year

                    # אם העדכון הוא בתחילת חודש, המדד הוא של החודש הקודם
                    cpi_lookup_month_for_history -= 1
                    if cpi_lookup_month_for_history == 0:
                        cpi_lookup_month_for_history = 12
                        cpi_lookup_year_for_history -= 1

                    cpi_for_history, cpi_base_desc_for_history = get_cpi_value_and_base(
                        cpi_lookup_year_for_history, cpi_lookup_month_for_history
                    )

                    if cpi_for_history is None or base_cpi_value is None or base_cpi_value == 0:
                        st.warning(
                            f"אין נתוני מדד היסטוריים או מדד בסיס חסר עבור {cpi_lookup_month_for_history:02d}/{cpi_lookup_year_for_history}. היסטוריית החישובים חלקית."
                        )
                        break

                    # חשוב: עבור היסטוריית העדכונים, אנחנו מניחים שמקדם הקשר שהוזן תקף לכל התקופה.
                    # במציאות, מקדמי קשר משתנים רק כאשר יש מעבר בסיס במדד.
                    # ההדמיה הזו אינה מורכבת מספיק כדי לטפל במקדמי קשר שונים לכל שינוי בסיס היסטורי.
                    calculated_amount = calculate_updated_mizono_with_link_factor(
                        base_mizono_amount, base_cpi_value, cpi_for_history, link_factor_input
                    )
                    
                    if calculated_amount is not None:
                        history_data.append(
                            {
                                "תאריך עדכון (הדמיה)": current_calc_date.strftime(
                                    "%d/%m/%Y"
                                ),
                                "מדד שהוצמד עליו": f"{cpi_lookup_month_for_history:02d}/{cpi_lookup_year_for_history} ({cpi_for_history:.2f})",
                                "סכום מעודכן": f"{calculated_amount:.2f} ש\"ח",
                            }
                        )

                    # התקדמות לתאריך העדכון הבא
                    next_calc_month = current_calc_date.month + update_frequency_months
                    next_calc_year = current_calc_date.year
                    while next_calc_month > 12:
                        next_calc_month -= 12
                        next_calc_year += 1
                    current_calc_date = datetime(next_calc_year, next_calc_month, 1)

                if history_data:
                    st.dataframe(pd.DataFrame(history_data), hide_index=True)
                else:
                    st.write("אין היסטוריית עדכונים להצגה כרגע.")


# הרצת האפליקציה
if __name__ == "__main__":
    main()
